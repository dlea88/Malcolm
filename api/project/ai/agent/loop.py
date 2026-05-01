"""Agent loop — multi-step tool-calling engine for Malcolm AI.

Hand-rolled ReAct-style loop: the LLM receives tool descriptions as
text, returns a structured StepDecision (which tool + params), we
execute the tool, feed the result back, and repeat until the LLM
says "final_answer" or we hit max_steps.

Works with any model that can produce structured JSON output via
any-llm's response_format (phi4, llama3.1:8b, Claude, GPT, etc.).
No dependency on native tool-calling APIs.
"""

import json
import logging
import os
import time

from ..llm.client import LLMClient
from ..enrichment import extract as extract_artifacts
from .models import StepDecision, AgentState, AgentResult
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are Malcolm AI, a network security investigation assistant.

You have access to tools that query Malcolm's services (OpenSearch, Arkime, Malcolm API).
At each step, you choose ONE tool to call and provide its parameters.

When you have enough information to answer the user's question, use tool="final_answer"
and put your response in the "answer" field.

Rules:
- Call ONE tool per step. Do not try to call multiple tools at once.
- Read the result of each tool call before deciding the next step.
- When searching for community_ids in Arkime, ALWAYS quote them: communityId == "1:abc..."
- Use the data from previous steps to inform your next tool call.
- If a result says "has_more: true", you can use tool="next_page" to get more results.
- If a tool returns an error, try a different approach or explain the issue in your final answer.
- Keep reasoning brief — one sentence explaining why you chose this tool.
"""


async def run_agent(query: str, llm: LLMClient, model_name: str,
                    tool_registry: ToolRegistry, surface: str = "",
                    workflow_steps: list[dict] | None = None,
                    max_steps: int = 5,
                    investigation: bool = False,
                    export_dir: str = "") -> AgentResult:
    """Run the agent loop.

    Args:
        query: User's natural language query
        llm: LLMClient instance
        model_name: Model to use (e.g. 'ollama-phi4')
        tool_registry: Registry of available tools
        surface: Current UI surface for context
        workflow_steps: Optional list of step definitions from workflow YAML
        max_steps: Maximum iterations before forced stop
        investigation: If True, auto-export investigation markdown on completion
        export_dir: Directory for investigation export files

    Returns:
        AgentResult with steps taken, answer, URLs, and data
    """
    t0 = time.monotonic()
    artifacts = extract_artifacts(query)
    state = AgentState(query=query, surface=surface, artifacts=artifacts.model_dump())

    total_workflow_steps = len(workflow_steps) if workflow_steps else 0

    for step_num in range(max_steps):
        # If we've completed all workflow steps, stop
        if workflow_steps and step_num >= total_workflow_steps:
            break

        # Build the prompt for this iteration
        messages = _build_messages(state, tool_registry, workflow_steps, step_num)

        # Resolve model: step-defined → workflow-defined → default
        step_model = model_name
        if workflow_steps and step_num < len(workflow_steps):
            step_model = workflow_steps[step_num].get("model") or model_name

        # LLM call — structured output
        try:
            result = await llm.acall(step_model, messages, response_model=StepDecision)
            decision = result.choices[0].message.parsed
        except Exception as e:
            logger.exception("Agent LLM call failed at step %d", step_num + 1)
            state.add_step(
                StepDecision(tool="error", reasoning=str(e)),
                {}, error=str(e),
            )
            break

        if not decision:
            state.add_step(
                StepDecision(tool="error", reasoning="LLM returned unparseable response"),
                {}, error="Failed to parse LLM response",
            )
            break

        # Check for final answer
        if decision.tool == "final_answer":
            state.add_step(decision, {"answer": decision.answer})
            break

        # Enforce tool constraints from workflow
        if workflow_steps and step_num < len(workflow_steps):
            allowed = workflow_steps[step_num].get("tools", [])
            if allowed and decision.tool not in allowed:
                # LLM picked a tool outside the constraint — force the first allowed tool
                logger.warning(
                    "Agent chose %s but step constrains to %s — forcing %s",
                    decision.tool, allowed, allowed[0],
                )
                decision.tool = allowed[0]

        # Execute the tool (or handle pagination)
        logger.info("Agent step %d: %s(%s)", step_num + 1, decision.tool, decision.params)

        if decision.tool == "next_page" and hasattr(state, '_last_paged_result'):
            # Pagination: return next chunk from the last large result
            tool_result = _get_next_page(state)
        else:
            tool_result = await tool_registry.execute(decision.tool, decision.params)

        # Post-process and paginate large results
        tool_result = _post_process_result(decision.tool, tool_result)
        tool_result = _paginate_result(tool_result, state)

        # Record the step
        error = tool_result.get("error", "")
        state.add_step(decision, tool_result, error=error)

        # Extract data that workflows might need
        _extract_workflow_data(state, tool_result, workflow_steps, step_num)

    duration_ms = int((time.monotonic() - t0) * 1000)
    agent_result = state.to_result(model_used=model_name)
    agent_result.data["duration_ms"] = duration_ms

    # Auto-export investigation markdown if configured
    if investigation and export_dir:
        _export_investigation(query, state, agent_result, export_dir)

    return agent_result


def _export_investigation(query, state, result, export_dir):
    """Write investigation markdown file."""
    import datetime

    os.makedirs(export_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_query = "".join(c if c.isalnum() or c in " -_" else "" for c in query)[:50].strip().replace(" ", "_")
    filename = f"{timestamp}_{safe_query}.md"
    filepath = os.path.join(export_dir, filename)

    lines = [
        f"# Investigation: {query}",
        f"",
        f"**Date:** {datetime.datetime.now().isoformat()}",
        f"**Model:** {result.model_used}",
        f"**Steps:** {result.total_steps}",
        f"",
    ]

    if state.urls:
        lines.append("## URLs")
        for key, url in state.urls.items():
            lines.append(f"- [{key}]({url})")
        lines.append("")

    if state.extracted:
        lines.append("## Extracted Data")
        for key, values in state.extracted.items():
            if isinstance(values, list):
                lines.append(f"- **{key}:** {', '.join(str(v) for v in values)}")
            else:
                lines.append(f"- **{key}:** {values}")
        lines.append("")

    lines.append("## Steps")
    lines.append("")
    for step in result.steps:
        lines.append(f"### Step {step.step}: {step.tool}")
        if step.reasoning:
            lines.append(f"**Reasoning:** {step.reasoning}")
        lines.append(f"**Params:** `{json.dumps(step.params)}`")
        if step.error:
            lines.append(f"**Error:** {step.error}")
        else:
            lines.append(f"**Result:** {step.result_summary[:200]}")
        lines.append("")

    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    result.data["investigation_file"] = filepath
    logger.info("Investigation exported to %s", filepath)


def _build_messages(state: AgentState, tool_registry: ToolRegistry,
                    workflow_steps: list[dict] | None,
                    step_num: int) -> list[dict]:
    """Build the message list for the LLM at this iteration."""

    # System prompt with tool descriptions
    if workflow_steps and step_num < len(workflow_steps):
        # Guided mode: workflow provides constrained tools and instructions
        step_def = workflow_steps[step_num]
        tool_names = step_def.get("tools", [])
        if tool_names:
            tool_text = tool_registry.describe_subset(tool_names)
        else:
            tool_text = tool_registry.describe_all()

        goal = step_def.get("goal", "")
        instructions = step_def.get("prompt", "")

        # Substitute {prev.field} references
        instructions = _substitute_prev(instructions, state)

        system = (
            f"{_SYSTEM_PROMPT}\n"
            f"Available tools:\n{tool_text}\n\n"
            f"YOUR TASK: {goal}\n"
            f"INSTRUCTIONS: {instructions}\n\n"
            f"You MUST use one of the tools listed above. "
            f"You MUST set tool to exactly one of: {', '.join(tool_names) if tool_names else 'any tool listed above'}. "
            f"Fill in the params based on the instructions."
        )
    else:
        # Free mode: all tools available, LLM decides
        tool_text = tool_registry.describe_all()
        system = f"{_SYSTEM_PROMPT}\nAvailable tools:\n{tool_text}"

    messages = [{"role": "system", "content": system}]

    # Add context from previous steps
    step_context = state.get_step_context()
    if step_context:
        messages.append({"role": "assistant", "content": step_context})

    # User query
    user_msg = state.query
    if state.surface:
        user_msg = f"[Current surface: {state.surface}] {user_msg}"
    if state.artifacts.get("ips"):
        user_msg += f"\n(Detected IPs: {', '.join(state.artifacts['ips'])})"

    messages.append({"role": "user", "content": user_msg})

    return messages


def _substitute_prev(text: str, state: AgentState) -> str:
    """Replace {prev.field} placeholders with extracted data from previous steps."""
    import re

    def replacer(match):
        field = match.group(1)
        value = state.extracted.get(field, f"(no {field} found yet)")
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)

    return re.sub(r'\{prev\.([\w.]+)\}', replacer, text)


def _post_process_result(tool_name: str, result: dict) -> dict:
    """Normalize tool results for consistent agent consumption."""
    if tool_name == "opensearch:search":
        # Flatten the OpenSearch response
        hits = result.get("hits", {})
        if isinstance(hits, dict):
            docs = [h.get("_source", h) for h in hits.get("hits", [])]
            return {
                "total": hits.get("total", {}).get("value", 0),
                "documents": docs[:20],  # limit for context window
            }
    elif tool_name == "arkime:list_sessions":
        # Normalize Arkime sessions response
        data = result.get("data", [])
        filtered = result.get("recordsFiltered", len(data))
        sessions = []
        for s in data[:20]:
            sessions.append({
                "id": s.get("id"),
                "source_ip": s.get("source", {}).get("ip"),
                "dest_ip": s.get("destination", {}).get("ip"),
                "protocols": s.get("ipProtocol"),
                "bytes": s.get("totBytes", s.get("totDataBytes", 0)),
            })
        # Build URLs from the expression that was used
        # (reconstructed from the original params via the calling context)
        return {
            "total": result.get("recordsTotal", 0),
            "filtered": filtered,
            "sessions": sessions,
        }
    elif tool_name == "arkime:build_query":
        if "error" in result and "esquery" not in result:
            return {"valid": False, "error": result["error"]}
        return {
            "valid": True,
            "sessions_url": result.get("sessions_url", ""),
            "pcap_url": result.get("pcap_url", ""),
        }

    return result


def _extract_workflow_data(state: AgentState, result: dict,
                           workflow_steps: list[dict] | None,
                           step_num: int):
    """Extract named fields from tool results for workflow {prev.field} substitution."""
    if not workflow_steps or step_num >= len(workflow_steps):
        return

    step_def = workflow_steps[step_num]
    extract_fields = step_def.get("extract", [])

    docs = result.get("documents", [])

    for field in extract_fields:
        values = set()
        for doc in docs:
            val = _deep_get(doc, field)
            if val is not None:
                if isinstance(val, list):
                    values.update(str(v) for v in val)
                else:
                    values.add(str(val))

        if values:
            state.extracted[field] = sorted(values)

    # Also extract from top-level result keys
    for field in extract_fields:
        if field in result and field not in state.extracted:
            state.extracted[field] = result[field]


def _deep_get(d: dict, dotted_key: str):
    """Get a value from a nested dict using dot notation."""
    keys = dotted_key.split(".")
    val = d
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
    return val


_PAGE_SIZE = 50


def _paginate_result(result: dict, state: AgentState) -> dict:
    """If result has more documents than PAGE_SIZE, return first page
    and store the rest for next_page requests."""
    docs = result.get("documents", [])
    sessions = result.get("sessions", [])
    items = docs or sessions
    key = "documents" if docs else "sessions"

    if len(items) <= _PAGE_SIZE:
        return result

    total = result.get("total", len(items))
    result[key] = items[:_PAGE_SIZE]
    result["page"] = 1
    result["page_size"] = _PAGE_SIZE
    result["has_more"] = len(items) > _PAGE_SIZE
    result["total"] = total
    result["showing"] = f"1-{_PAGE_SIZE} of {total}"

    # Store remaining items for next_page
    state._last_paged_result = {
        "key": key,
        "items": items,
        "page": 1,
        "total": total,
        "original_result": {k: v for k, v in result.items() if k != key},
    }

    return result


def _get_next_page(state: AgentState) -> dict:
    """Return the next page from a previously paginated result."""
    paged = getattr(state, '_last_paged_result', None)
    if not paged:
        return {"error": "No paginated result to continue"}

    page = paged["page"] + 1
    start = page * _PAGE_SIZE
    end = start + _PAGE_SIZE
    items = paged["items"][start:end]
    key = paged["key"]

    paged["page"] = page
    has_more = end < len(paged["items"])

    result = dict(paged["original_result"])
    result[key] = items
    result["page"] = page
    result["page_size"] = _PAGE_SIZE
    result["has_more"] = has_more
    result["total"] = paged["total"]
    result["showing"] = f"{start + 1}-{min(end, len(paged['items']))} of {paged['total']}"

    if not has_more:
        del state._last_paged_result

    return result

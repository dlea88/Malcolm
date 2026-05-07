"""Agent data models — Pydantic schemas for the agent loop.

StepDecision is what the LLM returns at each iteration.
AgentState tracks accumulated context across steps.
AgentResult is the final output.
"""

from pydantic import BaseModel, Field

from ..field_mappings import build_arkime_url, build_arkime_pcap_url


class StepDecision(BaseModel):
    """LLM's decision for a single agent loop iteration.

    The LLM receives tool descriptions as text and returns this
    structured response saying which tool to call and why.
    Use tool="final_answer" when the task is complete.
    """
    tool: str = Field(
        description='Tool to call (e.g. "opensearch:search", "arkime:list_sessions") '
                    'or "final_answer" when done'
    )
    params: dict = Field(
        default_factory=dict,
        description="Parameters for the tool call"
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this tool was chosen"
    )
    answer: str = Field(
        default="",
        description="Final answer text (only when tool=final_answer)"
    )


class StepRecord(BaseModel):
    """Record of one completed step in the agent loop."""
    step: int
    tool: str
    params: dict
    reasoning: str
    result_summary: str
    result_data: dict = Field(default_factory=dict)
    error: str = ""


class AgentResult(BaseModel):
    """Final output of the agent loop."""
    success: bool = True
    answer: str = ""
    steps: list[StepRecord] = Field(default_factory=list)
    urls: dict = Field(default_factory=dict)
    data: dict = Field(default_factory=dict)
    error: str = ""
    model_used: str = ""
    total_steps: int = 0


class AgentState:
    """Mutable state accumulated across agent loop iterations."""

    def __init__(self, query, surface="", artifacts=None):
        self.query = query
        self.surface = surface
        self.artifacts = artifacts or {}
        self.steps: list[StepRecord] = []
        self.extracted: dict = {}  # data carried between steps ({prev.field})
        self.urls: dict = {}

    def add_step(self, decision: StepDecision, result: dict, error: str = ""):
        """Record a completed step and extract key data."""
        summary = self._summarize_result(result)

        record = StepRecord(
            step=len(self.steps) + 1,
            tool=decision.tool,
            params=decision.params,
            reasoning=decision.reasoning,
            result_summary=summary,
            result_data=result,
            error=error,
        )
        self.steps.append(record)

        # Extract URLs from tool results
        for key in ("sessions_url", "pcap_url", "download_url"):
            if key in result:
                self.urls[key] = result[key]

        # Generate URLs from Arkime tool params if not already present
        if decision.tool in ("arkime:list_sessions", "arkime:build_query") and not error:
            expression = decision.params.get("expression", "")
            if expression:
                if "sessions_url" not in self.urls:
                    self.urls["sessions_url"] = build_arkime_url(expression)
                if "pcap_url" not in self.urls:
                    self.urls["pcap_url"] = build_arkime_pcap_url(expression)

    def _summarize_result(self, result: dict, max_len=300) -> str:
        """Create a concise summary of a tool result for the LLM context.

        Keeps key metadata (totals, counts, extracted fields) but drops
        the raw document array to avoid overwhelming small models.
        """
        import json
        summary = {}
        for key in ("total", "filtered", "valid", "error", "sessions_url", "pcap_url"):
            if key in result:
                summary[key] = result[key]

        # For document results, just list key extracted values
        docs = result.get("documents", [])
        if docs:
            summary["document_count"] = len(docs)
            # Show first doc's key fields
            first = docs[0] if docs else {}
            for field in ("rootId", "network", "source", "destination", "event", "rule"):
                if field in first:
                    val = first[field]
                    if isinstance(val, dict):
                        summary[f"first_doc.{field}"] = {k: v for k, v in list(val.items())[:3]}
                    else:
                        summary[f"first_doc.{field}"] = val

        sessions = result.get("sessions", [])
        if sessions:
            summary["session_count"] = len(sessions)

        text = json.dumps(summary, default=str)
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."

    def get_step_context(self) -> str:
        """Build context string from previous steps for the LLM."""
        if not self.steps:
            return ""

        lines = ["Previous steps and results:"]
        for s in self.steps:
            lines.append(f"\nStep {s.step}: Called {s.tool}")
            if s.reasoning:
                lines.append(f"  Reasoning: {s.reasoning}")
            if s.error:
                lines.append(f"  Error: {s.error}")
            else:
                lines.append(f"  Result: {s.result_summary}")
        return "\n".join(lines)

    def to_result(self, model_used="") -> AgentResult:
        """Convert accumulated state to final AgentResult."""
        # Use the last step's answer if it was a final_answer
        answer = ""
        if self.steps and self.steps[-1].tool == "final_answer":
            answer = self.steps[-1].result_data.get("answer", "") or self.steps[-1].reasoning

        return AgentResult(
            success=not any(s.error for s in self.steps),
            answer=answer,
            steps=self.steps,
            urls=self.urls,
            data=self.extracted,
            model_used=model_used,
            total_steps=len(self.steps),
        )

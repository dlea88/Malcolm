"""Malcolm MCP server — AI-powered orchestrator over Malcolm services.

This is the primary MCP interface. External agents connect here for
AI-assisted Malcolm operations. Internally, it:
  1. Builds context from surface + slash command + user query + field hints
  2. Runs configurable safety check via any-llm
  3. Asks LLM to select a tool and fill params
  4. Dispatches to service MCPs or direct APIs
  5. Returns structured results

Service MCPs (Arkime, OpenSearch, NetBox, Malcolm API) are also
independently connectable for direct, non-AI operations.
"""

import logging
import os

from mcp.server.fastmcp import FastMCP

from ..agent.loop import run_agent
from ..agent.tools import ToolRegistry
from ..agent.workflows import WorkflowRegistry
from ..audit import AuditLogger
from ..rag import (
    search_knowledge, create_knowledge_index, ingest_field_descriptions,
    index_exists as rag_index_exists,
)
from ..config import load_config
from ..config.models import ModelCatalog
from ..config.commands import CommandCatalog
from ..config.dashboards import DashboardCatalog
from ..config.prompts import PromptCatalog
from ..config.tools import ToolManifest
from ..context import merge_filters, inherit_time_range
from ..enrichment import extract as extract_artifacts
from ..llm.client import LLMClient
from ..llm.safety import SafetyGate
from ..llm.prompts import render_tool_prompt
from ..connections import (
    ARKIME_URL, ARKIME_AUTH, ARKIME_VERIFY_SSL,
    MALCOLM_API_URL, MALCOLM_API_AUTH,
)
from ..field_mappings import (
    ecs_filters_to_arkime_expression,
    build_arkime_url,
    build_netbox_url,
)

logger = logging.getLogger(__name__)

mcp = FastMCP(name="malcolm")

# Lazy-initialized singletons (created on first tool call)
_config = None
_llm = None
_safety = None
_audit = None
_tool_registry = None
_workflow_registry = None
_model_catalog = None
_command_catalog = None
_dashboard_catalog = None
_prompt_catalog = None
_tool_manifest = None


def _log_model_availability(catalog, default_model_name):
    """Log a one-line warning per unavailable model so misconfiguration is visible."""
    default_def = catalog.get(default_model_name)
    if not default_def:
        logger.warning(
            "AI default model '%s' is not defined in the catalog — set AI_DEFAULT_MODEL "
            "to one of: %s",
            default_model_name, ", ".join(sorted(catalog.all_models().keys())),
        )
    else:
        avail = default_def.availability()
        if avail['available']:
            logger.info("AI default model '%s' is configured and ready", default_model_name)
        else:
            logger.warning("AI default model unavailable — %s", avail['hint'])

    for name, model_def in sorted(catalog.all_models().items()):
        if name == default_model_name:
            continue
        avail = model_def.availability()
        if not avail['available']:
            logger.info("AI model '%s' unavailable — %s", name, avail['hint'])


def _init():
    """Initialize catalogs and LLM client on first use."""
    global _config, _llm, _safety, _audit, _tool_registry, _workflow_registry, _model_catalog, _command_catalog, _dashboard_catalog, _prompt_catalog, _tool_manifest

    if _config is not None:
        return

    _config = load_config()
    _model_catalog = ModelCatalog(default_model=_config.default_model)
    _log_model_availability(_model_catalog, _config.default_model)
    _command_catalog = CommandCatalog()
    _dashboard_catalog = DashboardCatalog()
    _prompt_catalog = PromptCatalog()
    _tool_manifest = ToolManifest()
    _llm = LLMClient(_model_catalog)
    _safety = SafetyGate(
        _llm, mode=_config.safety_mode,
        router_model=_config.router_model or None,
        tool_manifest=_tool_manifest,
    )
    _audit = AuditLogger(
        index_prefix=_config.audit_index,
        enabled=_config.audit_enabled,
    )
    _tool_registry = ToolRegistry()
    _workflow_registry = WorkflowRegistry()


@mcp.tool()
async def ask(query: str, surface: str = "", command_hint: str = "",
              filters: dict | None = None, time_from: str = "",
              time_to: str = "") -> dict:
    """Ask Malcolm a natural language question. The AI resolves intent,
    selects the appropriate service, and returns results.

    Args:
        query: Natural language question (e.g. 'show unusual TLS from OT subnet')
        surface: Current UI surface for context (arkime, opensearch_dashboards, netbox, strelka)
        command_hint: Optional slash command hint (/search, /filter, /pivot, etc.)
        filters: Optional existing filters from the current view
        time_from: Time range start
        time_to: Time range end
    """
    import time as _time
    _init()
    t0 = _time.monotonic()

    # 1. Extract artifacts (IPs, hashes, time ranges, protocols)
    artifacts = extract_artifacts(query)

    # 2. Parse slash command verb hint
    verb_hint = None
    if command_hint and command_hint.startswith("/"):
        verb_hint = command_hint[1:].strip().split(None, 1)[0].lower()

    # 3. Resolve model (default; tool-level override applied after selection)
    model_name = _config.default_model

    # 4. Build UI context object for prompt construction
    ui_context = _UIContext(
        current_tool=surface or None,
        existing_filters=filters or {},
        time_range_from=time_from or None,
        time_range_to=time_to or None,
    )

    # 5. Determine available tools based on surface
    all_tools = _get_all_tool_names()
    surface_tools = _get_surface_tools(surface) if surface else all_tools

    # 6. Build system prompt
    system_prompt = render_tool_prompt(
        dashboard_catalog=_dashboard_catalog,
        command_catalog=_command_catalog,
        ui_context=ui_context,
        artifacts=artifacts,
        verb_hint=verb_hint,
        prompt_catalog=_prompt_catalog,
        surface_tools=surface_tools,
        all_tools=all_tools,
    )

    # 7. Safety check + tool selection
    messages = [{"role": "user", "content": query}]
    selection = await _safety.acheck_and_select(messages, model_name, system_prompt)
    duration_ms = int((_time.monotonic() - t0) * 1000)

    if not selection.safe:
        await _audit.log_ask(
            query=query, surface=surface, model_used=model_name,
            tool_selected="", params={}, safe=False,
            safety_reason=selection.safety_reason, duration_ms=duration_ms,
        )
        return {
            "status": "blocked",
            "reason": selection.safety_reason,
        }

    # 8. Merge context: combine UI filters with LLM-selected filters
    if selection.params.get("filters") and filters:
        selection.params["filters"] = merge_filters(filters, selection.params["filters"])
    llm_time_from = selection.params.get("time_from", "")
    llm_time_to = selection.params.get("time_to", "")
    if llm_time_from or time_from:
        merged_from, merged_to = inherit_time_range(time_from, time_to, llm_time_from, llm_time_to)
        if merged_from:
            selection.params["time_from"] = merged_from
        if merged_to:
            selection.params["time_to"] = merged_to

    # 9. Audit log the successful resolution
    artifact_dict = artifacts.model_dump() if not artifacts.is_empty() else None
    await _audit.log_ask(
        query=query, surface=surface, model_used=model_name,
        tool_selected=selection.tool, params=selection.params,
        safe=True, artifacts=artifact_dict, duration_ms=duration_ms,
    )

    # 10. Return the selected tool and params for dispatch
    return {
        "status": "resolved",
        "tool": selection.tool,
        "params": selection.params,
        "model_used": model_name,
        "artifacts": artifact_dict,
    }


@mcp.tool()
async def explain_field(field_name: str, surface: str = "") -> dict:
    """Explain what a Malcolm field means, with example queries.

    Uses the field mapping catalog and prompt hints to provide context
    without an LLM call for basic lookups.

    Args:
        field_name: Field name in ECS or Arkime format (e.g. 'source.ip', 'ip.src')
        surface: Context surface for surface-specific examples
    """
    from ..field_mappings import ECS_TO_ARKIME, ARKIME_TO_ECS

    result = {"field": field_name, "found": False}

    # Check ECS -> Arkime mapping
    arkime_name = ECS_TO_ARKIME.get(field_name)
    if arkime_name:
        result["found"] = True
        result["ecs_name"] = field_name
        result["arkime_name"] = arkime_name

    # Check reverse mapping (Arkime -> ECS)
    ecs_name = ARKIME_TO_ECS.get(field_name)
    if ecs_name:
        result["found"] = True
        result["ecs_name"] = ecs_name
        result["arkime_name"] = field_name

    # Find dashboards that use this field
    _init()
    relevant_dashboards = []
    for entry in _dashboard_catalog.summary():
        profile = _dashboard_catalog.get_profile(entry["id"])
        if profile and field_name in profile.get("primary_fields", []):
            relevant_dashboards.append(entry["title"])
    if relevant_dashboards:
        result["dashboards"] = relevant_dashboards

    if not result["found"]:
        result["message"] = f"Field '{field_name}' not found in field mappings. It may still be valid -- check /mapi/fields for the full list."

    return result


@mcp.tool()
async def list_dashboards() -> dict:
    """List all available Malcolm dashboards with their keywords.

    Returns dashboard titles, UUIDs, and keywords for discovery. External
    agents can use this to understand what dashboards are available before
    opening one.
    """
    _init()
    dashboards = _dashboard_catalog.summary()
    return {
        "total": len(dashboards),
        "dashboards": dashboards,
    }


@mcp.tool()
async def get_dashboard_profile(dashboard: str) -> dict:
    """Get the semantic profile for a dashboard including data source,
    primary fields, field hints, and pivot affordances.

    Args:
        dashboard: Dashboard title or UUID
    """
    _init()

    # Try as UUID first, then search by title
    uuid = dashboard
    if not _dashboard_catalog.exists(uuid):
        uuid = _dashboard_catalog.resolve_by_title(dashboard)

    if not uuid or not _dashboard_catalog.exists(uuid):
        return {"found": False, "query": dashboard}

    entry = _dashboard_catalog.get(uuid)
    profile = _dashboard_catalog.get_profile(uuid)

    result = {
        "found": True,
        "id": uuid,
        "title": entry["title"],
        "keywords": entry.get("keywords", []),
    }
    if profile:
        result["profile"] = profile
    return result


@mcp.tool()
async def pivot(from_surface: str, to_surface: str,
                filters: dict | None = None,
                time_from: str = "", time_to: str = "") -> dict:
    """Pivot the current view to a different Malcolm surface.

    Translates the current filters and time range from one surface
    to another, building a navigation URL. Use this when an analyst
    wants to see the same data in a different tool.

    Args:
        from_surface: Current surface (arkime, opensearch_dashboards)
        to_surface: Target surface (arkime, opensearch_dashboards, netbox)
        filters: Current ECS filters (e.g. {"source.ip": "10.0.0.1", "network.protocol": "dns"})
        time_from: Current time range start
        time_to: Current time range end
    """
    _init()
    filters = filters or {}

    if to_surface == "arkime":
        expression = ecs_filters_to_arkime_expression(filters)

        # Validate via Arkime's buildquery API
        import httpx
        async with httpx.AsyncClient(verify=ARKIME_VERIFY_SSL, auth=ARKIME_AUTH, timeout=10.0) as client:
            resp = await client.post(
                f"{ARKIME_URL}/api/buildquery",
                json={"expression": expression, "date": "-1"},
            )
            if resp.status_code == 200:
                bq = resp.json()
                if "error" in bq:
                    return {"error": f"Invalid expression: {bq['error']}", "expression": expression}

        url = build_arkime_url(expression)
        return {"url": url, "surface": "arkime", "expression": expression, "validated": True}

    elif to_surface == "opensearch_dashboards":
        # Find the best dashboard for the current filters
        topic = "Connections"
        for field in filters:
            if "protocol" in field:
                proto = str(filters[field]).upper()
                results = _dashboard_catalog.search(proto)
                if results:
                    topic = results[0]["title"]
                    break
        dash_url = _dashboard_catalog.dashboard_url(
            _dashboard_catalog.resolve_by_title(topic) or "0ad3d7c2-3441-485e-9dfe-dbb22e84e576",
            time_from or "now-1d", time_to or "now",
        )
        return {"url": dash_url, "surface": "opensearch_dashboards", "topic": topic}

    elif to_surface == "netbox":
        ip = filters.get("source.ip", filters.get("destination.ip", ""))
        url = build_netbox_url(str(ip)) if ip else "/netbox/"
        return {"url": url, "surface": "netbox", "query": ip}

    return {"error": f"Unknown target surface: {to_surface}"}


# --- Investigation tools ---

_INVESTIGATIONS_DIR = os.environ.get(
    "MALCOLM_INVESTIGATIONS_DIR",
    os.path.join(os.path.dirname(__file__), "../../../../investigations"),
)


@mcp.tool()
async def list_investigations() -> list[dict]:
    """List completed investigation reports.

    Returns a list of investigation markdown files with their names
    and timestamps.
    """
    import glob
    inv_dir = os.path.normpath(_INVESTIGATIONS_DIR)
    if not os.path.isdir(inv_dir):
        return []

    results = []
    for path in sorted(glob.glob(os.path.join(inv_dir, "*.md")), reverse=True):
        name = os.path.splitext(os.path.basename(path))[0]
        stat = os.stat(path)
        results.append({
            "name": name,
            "file": os.path.basename(path),
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return results


@mcp.tool()
async def get_investigation(name: str) -> dict:
    """Read an investigation report.

    Args:
        name: Investigation filename (without .md extension)
    """
    inv_dir = os.path.normpath(_INVESTIGATIONS_DIR)
    safe_name = os.path.basename(name)  # prevent path traversal
    path = os.path.join(inv_dir, f"{safe_name}.md")

    if not os.path.exists(path):
        return {"error": f"Investigation '{name}' not found"}

    with open(path) as f:
        content = f.read()

    return {"name": name, "content": content}


# --- Workflow tools ---

@mcp.tool()
async def workflow(query: str, workflow_id: str = "") -> dict:
    """Run a multi-step investigation workflow.

    Uses the agent loop to chain tool calls guided by workflow YAML
    definitions. Each step uses the LLM to select the right tool and
    fill parameters based on results from previous steps.

    The query is matched to a workflow by keyword triggers, or you
    can specify a workflow_id directly.

    Available workflows: investigate_weird, investigate_alert, investigate_connection

    Args:
        query: Natural language description (e.g. 'investigate weird logs from 10.0.0.1')
        workflow_id: Optional workflow ID to run directly
    """
    _init()

    # Match workflow
    if workflow_id:
        wf = _workflow_registry.get(workflow_id)
    else:
        wf = _workflow_registry.match(query)

    if not wf:
        return {"error": f"No workflow matched: {query}",
                "available": _workflow_registry.list_workflows()}

    # Extract user params from query for {param} substitution
    artifacts = extract_artifacts(query)
    user_params = {}
    if artifacts.ips:
        user_params["ip"] = artifacts.ips[0]
        user_params["query_filter"] = f"source.ip:{artifacts.ips[0]}"

    # Run agent loop with workflow steps
    # Model resolution: workflow-level model → global default
    workflow_model = wf.model or _config.default_model
    # Check if workflow wants auto-investigation
    inv_dir = os.path.normpath(_INVESTIGATIONS_DIR) if wf.investigation else ""

    result = await run_agent(
        query=query,
        llm=_llm,
        model_name=workflow_model,
        tool_registry=_tool_registry,
        workflow_steps=wf.get_steps(user_params),
        max_steps=wf.max_steps,
        investigation=wf.investigation,
        export_dir=inv_dir,
    )

    await _audit.log("workflow_executed",
                     workflow=wf.id, query=query,
                     success=result.success,
                     total_steps=result.total_steps)

    return result.model_dump()


# --- RAG / Knowledge tools ---

@mcp.tool()
async def search_fields(query: str, k: int = 5) -> list[dict]:
    """Search Malcolm field documentation using natural language.

    Uses semantic search over the knowledge base to find relevant fields.
    The knowledge base must be built first using build_knowledge_base.

    Args:
        query: Natural language query (e.g. 'fields related to DNS queries')
        k: Number of results (default: 5)
    """
    if not await rag_index_exists():
        return [{"error": "Knowledge base not built. Run build_knowledge_base first."}]

    results = await search_knowledge(query, k=k)
    return results


async def build_knowledge_base() -> dict:
    """Build the Malcolm knowledge base for semantic field search.

    Ingests field descriptions from Malcolm's /mapi/fields endpoint
    into a k-NN vector index in OpenSearch. Uses Ollama for embeddings.
    This is a one-time operation (or re-run when field schema changes).
    """
    _init()
    await create_knowledge_index()
    count = await ingest_field_descriptions(MALCOLM_API_URL, MALCOLM_API_AUTH)

    return {
        "status": "built",
        "fields_ingested": count,
        "index": "malcolm_knowledge",
    }


# --- Internal helpers ---

class _UIContext:
    """Minimal UI context for prompt construction."""
    def __init__(self, current_tool=None, dashboard_id=None,
                 existing_filters=None, time_range_from=None, time_range_to=None):
        self.current_tool = current_tool
        self.dashboard_id = dashboard_id
        self.existing_filters = existing_filters or {}
        self.time_range_from = time_range_from
        self.time_range_to = time_range_to


def _get_all_tool_names():
    """Return all UI-level tool names from the tool manifest."""
    return _tool_manifest.tool_names()


def _get_surface_tools(surface):
    """Return tool names available on a specific surface."""
    all_tools = _get_all_tool_names()
    return [t for t in all_tools if t.startswith(f"{surface}:")]

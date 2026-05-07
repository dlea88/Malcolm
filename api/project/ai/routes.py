"""Flask API routes for the AI chat widget.

These routes bridge HTTP requests from the browser widget to the
MCP-based AI tools. The widget calls these endpoints; the routes
call the MCP tools internally.

Endpoints:
  GET  /mapi/ai/v1/status    — Is AI enabled? Widget checks this before rendering.
  POST /mapi/ai/v1/intent    — Process a natural language query.
  GET  /mapi/ai/v1/commands  — List available slash commands.
  POST /mapi/ai/v1/workflow  — Run a multi-step workflow.
"""

import asyncio
import collections
import logging
import os
import time

from flask import Blueprint, current_app, jsonify, request

from .connections import AI_EXECUTE_ENABLED
from .field_mappings import (
    ecs_filters_to_arkime_expression,
    build_arkime_url,
    build_netbox_url,
)

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__)

# Rate limiter for /execute — tracks calls per user per minute
_rate_limit_window = 60  # seconds
_rate_limit_max = int(os.environ.get("AI_EXECUTE_RATE_LIMIT", "30"))  # calls per window
_rate_limit_buckets: dict[str, list[float]] = collections.defaultdict(list)


def _check_rate_limit(user_id: str) -> bool:
    """Returns True if the request is within rate limits."""
    now = time.time()
    bucket = _rate_limit_buckets[user_id]
    # Prune old entries
    _rate_limit_buckets[user_id] = [t for t in bucket if now - t < _rate_limit_window]
    if len(_rate_limit_buckets[user_id]) >= _rate_limit_max:
        return False
    _rate_limit_buckets[user_id].append(now)
    return True

# Lazy-loaded MCP tool functions (avoids circular imports)
_initialized = False
_ask_fn = None
_workflow_fn = None
_explain_field_fn = None
_search_fields_fn = None
_command_catalog = None
_tool_manifest = None


def _ensure_init():
    """Lazy-load the MCP tool internals."""
    global _initialized, _ask_fn, _workflow_fn
    global _explain_field_fn, _search_fields_fn, _command_catalog, _tool_manifest
    if _initialized:
        return

    from .mcp.malcolm_server import (
        ask, workflow, explain_field, search_fields,
        _init as mcp_init,
    )
    from .config.commands import CommandCatalog
    from .config.tools import ToolManifest

    mcp_init()
    _ask_fn = ask
    _workflow_fn = workflow
    _explain_field_fn = explain_field
    _search_fields_fn = search_fields
    _command_catalog = CommandCatalog()
    _tool_manifest = ToolManifest()
    _initialized = True


def _run_async(coro):
    """Run an async function from sync Flask context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=120)
    else:
        return asyncio.run(coro)


@ai_bp.route("/status", methods=["GET"])
def status():
    """Check if AI features are enabled."""
    from . import config
    from .config.models import ModelCatalog
    try:
        cfg = config.load_config()
        catalog = ModelCatalog(default_model=cfg.default_model)
        default_def = catalog.get(cfg.default_model)
        default_availability = (
            default_def.availability()
            if default_def
            else {
                "available": False,
                "missing": [],
                "hint": f"default model '{cfg.default_model}' is not defined in the catalog",
            }
        )
        return jsonify({
            "enabled": True,
            "default_model": cfg.default_model,
            "default_model_availability": default_availability,
            "available_models": sorted(catalog.available_models().keys()),
            "safety_mode": cfg.safety_mode,
            "execute_enabled": AI_EXECUTE_ENABLED,
        })
    except Exception as e:
        logger.exception("AI status check failed")
        return jsonify({"enabled": False, "error": str(e)})


_UNKNOWN_MESSAGES = {
    "off_topic": "That request isn't related to Malcolm network analysis. Try asking about traffic, sessions, alerts, or assets.",
    "unsafe": "Request blocked by safety check.",
    "no_tool": "Malcolm doesn't have a tool for that. Try asking about traffic, sessions, dashboards, or assets.",
}


@ai_bp.route("/ask", methods=["POST"])
def ask_route():
    """Process a natural language query.

    Two modes (set via 'mode' in request body):
      navigate (default): Returns URL + auto_navigate for the widget.
      data: Returns raw tool selection for MCP/API consumers.

    Request body:
        query: str — The user's natural language query
        mode: str — 'navigate' (default) or 'data'
        current_context: dict — UI context (surface, dashboard, filters, time)
    """
    _ensure_init()
    t0 = time.monotonic()

    data = request.get_json() or {}
    query = data.get("query", "").strip()
    mode = data.get("mode", "navigate")
    if not query:
        return jsonify({"error": "Empty query"}), 400
    if len(query) > 2000:
        return jsonify({"error": "Query too long (max 2000 chars)"}), 400

    ctx = data.get("current_context", {})
    surface = ctx.get("current_tool", "")
    command_hint = ""
    filters = ctx.get("existing_filters", {})
    time_from = ctx.get("time_range_from", "")
    time_to = ctx.get("time_range_to", "")

    if query.startswith("/"):
        parts = query.split(None, 1)
        command_hint = parts[0]
        query = parts[1] if len(parts) > 1 else ""

    try:
        result = _run_async(_ask_fn(
            query=query,
            surface=surface,
            command_hint=command_hint,
            filters=filters if filters else None,
            time_from=time_from,
            time_to=time_to,
        ))
    except Exception as e:
        logger.exception("Ask processing failed")
        return jsonify({"error": str(e)}), 500

    duration_ms = int((time.monotonic() - t0) * 1000)

    if not result or not isinstance(result, dict):
        return jsonify({"error": "LLM returned no result", "duration_ms": duration_ms}), 500

    tool = result.get("tool", "")
    status = result.get("status", "error")

    # === Data mode: raw tool selection for MCP/API consumers ===
    if mode == "data":
        return jsonify({
            "status": status,
            "tool": tool,
            "params": result.get("params", {}),
            "model_used": result.get("model_used", ""),
            "artifacts": result.get("artifacts"),
            "duration_ms": duration_ms,
        })

    # === Execute mode: pick tool AND run it, return data ===
    if mode == "execute" and not AI_EXECUTE_ENABLED:
        return jsonify({"error": "Execute mode is disabled. Set AI_EXECUTE_ENABLED=true."}), 403
    if mode == "execute" and status == "resolved" and tool and tool != "unknown":
        from .agent.tools import ToolRegistry
        registry = ToolRegistry()

        # Map navigation tool names to executable service tools
        exec_tool, exec_params = _tool_manifest.resolve(tool, result.get("params", {}))

        try:
            tool_data = _run_async(registry.execute(exec_tool, exec_params))
        except Exception as e:
            logger.exception("Execute mode tool execution failed: %s", exec_tool)
            tool_data = {"error": str(e)}

        return jsonify({
            "status": "ok" if "error" not in tool_data else "error",
            "tool": tool,
            "params": result.get("params", {}),
            "data": tool_data,
            "model_used": result.get("model_used", ""),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })

    # === Navigate mode: URL + action for widget ===
    if status == "resolved" and tool and tool != "unknown":
        nav_params = dict(result.get("params", {}))
        # Inject dashboard context so _build_action can rebuild filter URLs
        if ctx.get("dashboard_id"):
            nav_params["_dashboard_id"] = ctx["dashboard_id"]
        action = _build_action(tool, nav_params)
        if action:
            return jsonify({
                "status": "resolved",
                "intent": tool,
                "actions": [action],
                "auto_navigate": True,
                "message": action.get("description", f"Opening {tool}"),
                "duration_ms": duration_ms,
            })

    # Blocked or unknown
    if status == "blocked":
        msg = _UNKNOWN_MESSAGES["unsafe"]
    elif tool == "unknown":
        msg = _UNKNOWN_MESSAGES["off_topic"]
    else:
        msg = _UNKNOWN_MESSAGES["no_tool"]

    return jsonify({
        "status": "error",
        "intent": tool,
        "message": msg,
        "auto_navigate": False,
        "duration_ms": duration_ms,
    })


@ai_bp.route("/commands", methods=["GET"])
def commands():
    """Return available slash commands."""
    _ensure_init()
    return jsonify({
        "commands": _command_catalog.slash_commands(),
    })


@ai_bp.route("/workflow", methods=["POST"])
def workflow_route():
    """Run a multi-step investigation workflow via the agent loop.

    Request body:
        query: str — Natural language description
        workflow_id: str — Optional workflow ID
    """
    _ensure_init()

    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400

    try:
        result = _run_async(_workflow_fn(
            query=query,
            workflow_id=data.get("workflow_id", ""),
        ))
    except Exception as e:
        logger.exception("Workflow execution failed")
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


@ai_bp.route("/execute", methods=["POST"])
def execute_route():
    """Execute a tool directly and return the results.

    Disabled by default. Set AI_EXECUTE_ENABLED=true to enable.
    Rate-limited per user (AI_EXECUTE_RATE_LIMIT, default 30/min).
    All calls audit-logged.

    Request body:
        tool: str — Tool name (e.g. 'opensearch:search', 'arkime:list_sessions')
        params: dict — Tool parameters
    """
    # Gate: disabled by default
    if not AI_EXECUTE_ENABLED:
        return jsonify({"error": "Tool execution endpoint is disabled. Set AI_EXECUTE_ENABLED=true to enable."}), 403

    _ensure_init()

    # Rate limit by authenticated user
    user_id = request.authorization.username if request.authorization else "anonymous"
    if not _check_rate_limit(user_id):
        logger.warning("Rate limit exceeded for user %s on /execute", user_id)
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429

    data = request.get_json() or {}
    tool = data.get("tool", "").strip()
    params = data.get("params", {})

    if not tool:
        return jsonify({"error": "Missing 'tool' field"}), 400

    from .agent.tools import ToolRegistry
    registry = ToolRegistry()

    if not registry.get(tool):
        return jsonify({
            "error": f"Unknown tool: {tool}",
            "available": registry.list_tools(),
        }), 400

    # Audit log
    if os.environ.get("AI_AUDIT_ENABLED", "false").lower() in ("true", "1", "yes"):
        from .audit import AuditLogger
        audit = AuditLogger(enabled=True)
        _run_async(audit.log("execute", user=user_id, tool=tool, params=params))

    try:
        result = _run_async(registry.execute(tool, params))
    except Exception as e:
        logger.exception("Tool execution failed")
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "tool": tool,
        "status": "ok" if "error" not in result else "error",
        "data": result,
    })



def _build_action(tool, params):
    """Build a navigation action from a tool selection."""
    from .config.dashboards import DashboardCatalog

    if tool == "arkime:open":
        expression = ecs_filters_to_arkime_expression(params.get("filters", {}))
        url = build_arkime_url(expression)
        return {"type": "url", "url": url, "description": f"Arkime: {expression}"}

    elif tool == "opensearch_dashboards:open":
        topic = params.get("topic", "Overview")
        dc = DashboardCatalog()
        results = dc.search(topic)
        if results:
            uuid = results[0]["id"]
            url = dc.dashboard_url_with_filters(
                uuid, filters=params.get("filters") or None,
                from_time=params.get("time_from", "now-1d"),
                to_time=params.get("time_to", "now"),
            )
            return {"type": "url", "url": url, "description": f"Dashboard: {topic}"}

    elif tool == "netbox:search":
        query = params.get("query", "")
        url = build_netbox_url(query)
        return {"type": "url", "url": url, "description": f"NetBox: {query}"}

    elif tool == "opensearch_dashboards:filter":
        # Rebuild the current dashboard URL with new filters/time applied
        dc = DashboardCatalog()
        dashboard_id = params.get("_dashboard_id")  # injected by ask_route from context
        if dashboard_id and dc.exists(dashboard_id):
            new_filters = params.get("filters", {})
            url = dc.dashboard_url_with_filters(
                dashboard_id, filters=new_filters or None,
                from_time=params.get("time_from"),
                to_time=params.get("time_to"),
            )
            return {"type": "url", "url": url, "description": "Apply filter"}
        return {"type": "filter", "params": params, "description": "Apply filter"}

    elif tool == "arkime:export":
        expression = params.get("expression", "")
        url = build_arkime_url(expression)
        return {"type": "url", "url": url, "description": f"Arkime export: {expression}"}

    logger.warning("No navigation action for tool '%s' — use execute mode or API", tool)
    return {"type": "info", "description": f"Tool {tool} selected but no navigation available"}

"""Audit logger — records AI interactions to OpenSearch.

Every LLM call, tool selection, and safety gate decision is logged to
the malcolm_ai_audit-* index for compliance and reproducibility.
"""

import datetime
import logging
import os
import uuid

import httpx

from .connections import OPENSEARCH_URL as _OS_URL, OPENSEARCH_AUTH as _OS_AUTH, OPENSEARCH_VERIFY_SSL as _OS_VERIFY

logger = logging.getLogger(__name__)


class AuditLogger:
    """Logs AI interactions to OpenSearch."""

    def __init__(self, index_prefix='malcolm_ai_audit', enabled=True):
        self._index_prefix = index_prefix
        self._enabled = enabled

    def _index_name(self):
        return f"{self._index_prefix}-{datetime.date.today().strftime('%Y.%m.%d')}"

    async def log(self, event_type, **fields):
        """Log an audit event to OpenSearch.

        Args:
            event_type: Event type (e.g. 'ask', 'safety_check', 'tool_call')
            **fields: Event-specific fields to log
        """
        if not self._enabled:
            return

        doc = {
            "@timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            **fields,
        }

        try:
            async with httpx.AsyncClient(verify=_OS_VERIFY, auth=_OS_AUTH, timeout=5.0) as client:
                resp = await client.post(
                    f"{_OS_URL}/{self._index_name()}/_doc",
                    json=doc,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code not in (200, 201):
                    logger.warning("Audit log failed: %s %s", resp.status_code, resp.text[:200])
        except Exception:
            logger.exception("Failed to write audit log")

    async def log_ask(self, query, surface, model_used, tool_selected,
                      params, safe, safety_reason=None, artifacts=None,
                      duration_ms=None):
        """Log an ask tool invocation."""
        await self.log(
            "ask",
            query=query,
            surface=surface,
            model_used=model_used,
            tool_selected=tool_selected,
            params=params,
            safe=safe,
            safety_reason=safety_reason,
            artifacts=artifacts,
            duration_ms=duration_ms,
        )

    async def log_tool_call(self, server, tool, params, success, error=None,
                            duration_ms=None):
        """Log a service MCP tool call."""
        await self.log(
            "tool_call",
            server=server,
            tool=tool,
            params=params,
            success=success,
            error=error,
            duration_ms=duration_ms,
        )

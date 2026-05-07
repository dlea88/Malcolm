"""Malcolm API MCP server — wraps /mapi/ endpoints.

Provides schema-aware query construction, aggregation, and health
checks by calling the Malcolm API internally on the Docker network.
"""

import logging
import os

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(name="malcolm-api")

MALCOLM_API_URL = os.environ.get('MALCOLM_API_URL', 'http://api:5000')
MALCOLM_API_PREFIX = os.environ.get('MALCOLM_API_PREFIX', 'mapi')
MALCOLM_API_VERIFY_SSL = os.environ.get('MALCOLM_API_VERIFY_SSL', 'false').lower() == 'true'

_api_user = os.environ.get('MALCOLM_API_USER', '')
_api_pass = os.environ.get('MALCOLM_API_PASSWORD', '')
MALCOLM_API_AUTH = (_api_user, _api_pass) if _api_user else None


def _url(endpoint):
    return f"{MALCOLM_API_URL}/{MALCOLM_API_PREFIX}/{endpoint.lstrip('/')}"


def _client():
    return httpx.AsyncClient(
        verify=MALCOLM_API_VERIFY_SSL,
        auth=MALCOLM_API_AUTH,
        timeout=30.0,
        follow_redirects=True,
    )


@mcp.tool()
async def malcolm_list_fields(index_type: str = "") -> dict:
    """List all available fields across Malcolm indices.

    Essential for schema-aware query construction. Returns field names,
    types, and descriptions.

    Args:
        index_type: Filter by index type ('network' or 'other'). Empty returns all.
    """
    params = {}
    if index_type:
        params["doctype"] = index_type

    async with _client() as client:
        resp = await client.get(_url("fields"), params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def malcolm_aggregate(field: str, query: str = "",
                            start_time: str = "", end_time: str = "",
                            limit: int = 10) -> dict:
    """Quick summary aggregation on a field.

    Args:
        field: Field to aggregate (e.g. 'source.ip', 'network.protocol')
        query: Optional Lucene filter query
        start_time: Start of time range
        end_time: End of time range
        limit: Number of top values to return (default: 10)
    """
    params = {"limit": limit}
    if query:
        params["filter"] = query
    if start_time:
        params["from"] = start_time
    if end_time:
        params["to"] = end_time

    async with _client() as client:
        resp = await client.get(_url(f"agg/{field}"), params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def malcolm_ingest_health() -> dict:
    """Check ingestion health across hosts.

    Useful for detecting when missing data is an ingestion problem
    rather than a detection gap. Returns per-host latest ingest
    timestamps.
    """
    async with _client() as client:
        resp = await client.get(_url("ingest-stats"))
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def malcolm_document(index: str, doc_id: str) -> dict:
    """Retrieve a specific document by index and ID.

    Args:
        index: OpenSearch index name
        doc_id: Document ID
    """
    params = {"index": index, "id": doc_id}

    async with _client() as client:
        resp = await client.get(_url("document"), params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def malcolm_version() -> dict:
    """Get Malcolm version and system health information."""
    async with _client() as client:
        resp = await client.get(_url(""))
        resp.raise_for_status()
        return resp.json()

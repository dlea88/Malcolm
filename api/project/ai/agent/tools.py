"""Tool registry — describes and executes tools for the agent loop.

Tools are the service MCP operations (Arkime, OpenSearch, Malcolm API,
NetBox) exposed as callable functions. The registry provides text
descriptions for the LLM prompt and executes tool calls via httpx.
"""

import logging

import httpx

from ..connections import (
    ARKIME_URL, ARKIME_AUTH, ARKIME_VERIFY_SSL,
    OPENSEARCH_URL, OPENSEARCH_AUTH, OPENSEARCH_VERIFY_SSL,
    MALCOLM_API_URL, MALCOLM_API_AUTH, MALCOLM_API_PREFIX,
    NETBOX_URL, NETBOX_AUTH,
)

logger = logging.getLogger(__name__)


class ToolDef:
    """A tool the agent can call."""

    def __init__(self, name, description, service, endpoint_builder):
        self.name = name
        self.description = description
        self.service = service
        self._endpoint_builder = endpoint_builder

    def build_request(self, params):
        """Build (method, url, kwargs) for the httpx call."""
        return self._endpoint_builder(params)


class ToolRegistry:
    """Registry of tools available to the agent."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._arkime_url = ARKIME_URL
        self._opensearch_url = OPENSEARCH_URL
        self._malcolm_api_url = MALCOLM_API_URL
        self._malcolm_api_prefix = MALCOLM_API_PREFIX
        self._netbox_url = NETBOX_URL

        self._os_auth = OPENSEARCH_AUTH
        self._ark_auth = ARKIME_AUTH
        self._api_auth = MALCOLM_API_AUTH
        self._nb_auth = NETBOX_AUTH

        self._verify_ssl = False  # internal communication usually bypasses strict SSL for ease of deployment

        self._register_defaults()

    def _register_defaults(self):
        """Register the standard Malcolm service tools."""

        self.register(ToolDef(
            name="opensearch:search",
            description=(
                "Search OpenSearch for network log documents. "
                "Params: query (Lucene string, e.g. 'event.dataset:weird AND source.ip:10.0.0.1'), "
                "index (default: arkime_sessions3-*), limit (default: 100). "
                "Returns: total count and matching documents."
            ),
            service="opensearch",
            endpoint_builder=self._build_opensearch_search,
        ))

        self.register(ToolDef(
            name="opensearch:aggregate",
            description=(
                "Run an aggregation on a field in OpenSearch. "
                "Params: field (e.g. 'network.protocol'), size (default: 10), "
                "query (optional Lucene filter). "
                "Returns: top value buckets with counts."
            ),
            service="opensearch",
            endpoint_builder=self._build_opensearch_aggregate,
        ))

        self.register(ToolDef(
            name="arkime:list_sessions",
            description=(
                "Search Arkime sessions with an expression. "
                "Params: expression (Arkime syntax, e.g. 'ip.src == 10.0.0.1 && protocols == dns'), "
                "limit (default: 100). "
                "IMPORTANT: Quote values with special characters: communityId == \"1:abc+def=\". "
                "Returns: session count, session data, sessions_url, pcap_url."
            ),
            service="arkime",
            endpoint_builder=self._build_arkime_sessions,
        ))

        self.register(ToolDef(
            name="arkime:build_query",
            description=(
                "Validate an Arkime expression and compile to OpenSearch DSL. "
                "Params: expression (Arkime syntax). "
                "Returns: valid flag, compiled query, sessions_url, pcap_url."
            ),
            service="arkime",
            endpoint_builder=self._build_arkime_query,
        ))

        self.register(ToolDef(
            name="arkime:get_session_detail",
            description=(
                "Get full detail for a single Arkime session. "
                "Params: session_id (from list_sessions results), node (Arkime node name). "
                "Returns: complete session metadata, packet info, protocol details."
            ),
            service="arkime",
            endpoint_builder=self._build_arkime_session_detail,
        ))

        self.register(ToolDef(
            name="arkime:export_pcap",
            description=(
                "Build a PCAP download URL for matching sessions. "
                "Params: expression (Arkime syntax). "
                "Returns: download URL. This is a high-cost operation."
            ),
            service="arkime",
            endpoint_builder=self._build_arkime_export_pcap,
        ))

        self.register(ToolDef(
            name="arkime:hunt_create",
            description=(
                "Create an Arkime hunt job to search packet payloads for strings or patterns. "
                "Params: search_text (text to find), expression (optional Arkime filter), "
                "search_type ('ascii', 'hex', or 'regex'). "
                "Returns: hunt_id. Poll arkime:hunt_status for results. "
                "This is a high-cost operation."
            ),
            service="arkime",
            endpoint_builder=self._build_arkime_hunt_create,
        ))

        self.register(ToolDef(
            name="arkime:hunt_status",
            description=(
                "Check the status of an Arkime hunt job. "
                "Params: hunt_id (from hunt_create). "
                "Returns: status, matched sessions count, searched count."
            ),
            service="arkime",
            endpoint_builder=self._build_arkime_hunt_status,
        ))

        self.register(ToolDef(
            name="opensearch:list_fields",
            description=(
                "List available fields and their types for an index. "
                "Params: index (default: arkime_sessions3-*). "
                "Returns: field names and types. Use this to discover what fields exist."
            ),
            service="opensearch",
            endpoint_builder=self._build_opensearch_list_fields,
        ))

        self.register(ToolDef(
            name="netbox:lookup_device",
            description=(
                "Look up a device, IP address, or asset in NetBox. "
                "Params: query (IP address, hostname, device name, or CIDR). "
                "Returns: device name, role, site, status, primary IP."
            ),
            service="netbox",
            endpoint_builder=self._build_netbox_lookup,
        ))

        self.register(ToolDef(
            name="opensearch:list_anomaly_detectors",
            description=(
                "List configured anomaly detectors in OpenSearch. "
                "Returns: detector names, IDs, monitored indices, feature names. "
                "Use this to discover what anomaly detection is available."
            ),
            service="opensearch",
            endpoint_builder=self._build_opensearch_list_detectors,
        ))

        self.register(ToolDef(
            name="opensearch:get_anomaly_results",
            description=(
                "Get anomaly detection results for a detector. "
                "Params: detector_id (from list_anomaly_detectors), "
                "start_time (default: now-7d), end_time (default: now). "
                "Returns: anomaly grades, confidence scores, timestamps."
            ),
            service="opensearch",
            endpoint_builder=self._build_opensearch_anomaly_results,
        ))

        self.register(ToolDef(
            name="netbox:list_prefixes",
            description=(
                "List IP subnets/prefixes from NetBox. "
                "Params: site (optional site slug), vlan (optional VLAN ID). "
                "Use to determine what subnet an IP belongs to."
            ),
            service="netbox",
            endpoint_builder=self._build_netbox_prefixes,
        ))

        self.register(ToolDef(
            name="netbox:get_interfaces",
            description=(
                "Get network interfaces for a device from NetBox. "
                "Params: device (device name). "
                "Returns: interface names, MAC addresses, enabled status."
            ),
            service="netbox",
            endpoint_builder=self._build_netbox_interfaces,
        ))

        self.register(ToolDef(
            name="malcolm_api:list_fields",
            description=(
                "List all available fields across Malcolm indices. "
                "Returns: field names, types, and count."
            ),
            service="malcolm_api",
            endpoint_builder=self._build_api_fields,
        ))

        self.register(ToolDef(
            name="malcolm_api:aggregate",
            description=(
                "Quick aggregation via Malcolm API. "
                "Params: field (e.g. 'event.provider'). "
                "Returns: top values and counts."
            ),
            service="malcolm_api",
            endpoint_builder=self._build_api_aggregate,
        ))

    def register(self, tool: ToolDef):
        self._tools[tool.name] = tool

    def get(self, name) -> ToolDef | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def describe_all(self) -> str:
        """Build a text description of all tools for the LLM prompt."""
        lines = []
        for i, (name, tool) in enumerate(self._tools.items(), 1):
            lines.append(f"{i}. {name} — {tool.description}")
        return "\n".join(lines)

    def describe_subset(self, tool_names: list[str]) -> str:
        """Build descriptions for a specific set of tools."""
        lines = []
        for i, name in enumerate(tool_names, 1):
            tool = self._tools.get(name)
            if tool:
                lines.append(f"{i}. {name} — {tool.description}")
        return "\n".join(lines)

    async def execute(self, tool_name: str, params: dict) -> dict:
        """Execute a tool call and return the result."""
        tool = self._tools.get(tool_name)
        if not tool:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            method, url, kwargs = tool.build_request(params)
            auth = self._auth_for_service(tool.service)

            async with httpx.AsyncClient(
                verify=self._verify_ssl, auth=auth, timeout=30.0
            ) as client:
                if method == "GET":
                    resp = await client.get(url, **kwargs)
                else:
                    resp = await client.post(url, **kwargs)
                resp.raise_for_status()
                return resp.json()

        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def _auth_for_service(self, service):
        if service == "opensearch":
            return self._os_auth
        elif service == "arkime":
            return self._ark_auth
        elif service == "malcolm_api":
            return self._api_auth
        elif service == "netbox":
            return self._nb_auth
        return None

    # --- Request builders ---

    def _build_opensearch_search(self, params):
        index = params.get("index", "arkime_sessions3-*")
        query = params.get("query", "*")
        limit = params.get("limit", 100)
        body = {
            "size": limit,
            "query": {"query_string": {"query": query}},
            "sort": [{"@timestamp": {"order": "desc"}}],
        }
        return ("POST", f"{self._opensearch_url}/{index}/_search",
                {"json": body, "headers": {"Content-Type": "application/json"}})

    def _build_opensearch_aggregate(self, params):
        index = params.get("index", "arkime_sessions3-*")
        field = params.get("field", "event.dataset")
        size = params.get("size", 10)
        body = {
            "size": 0,
            "aggs": {"result": {"terms": {"field": field, "size": size}}},
        }
        query = params.get("query")
        if query:
            body["query"] = {"query_string": {"query": query}}
        return ("POST", f"{self._opensearch_url}/{index}/_search",
                {"json": body, "headers": {"Content-Type": "application/json"}})

    def _build_arkime_sessions(self, params):
        expression = params.get("expression", "*")
        limit = params.get("limit", 100)
        qp = {"expression": expression, "length": limit, "date": "-1"}
        return ("GET", f"{self._arkime_url}/api/sessions", {"params": qp})

    def _build_arkime_query(self, params):
        expression = params.get("expression", "*")
        body = {"expression": expression, "date": "-1"}
        return ("POST", f"{self._arkime_url}/api/buildquery", {"json": body})

    def _build_api_fields(self, params):
        url = f"{self._malcolm_api_url}/{self._malcolm_api_prefix}/fields"
        return ("GET", url, {"params": {}})

    def _build_api_aggregate(self, params):
        field = params.get("field", "event.provider")
        url = f"{self._malcolm_api_url}/{self._malcolm_api_prefix}/agg"
        return ("GET", url, {"params": {"field": field}, "follow_redirects": True})

    def _build_arkime_hunt_create(self, params):
        body = {
            "totalSessions": 0,
            "name": f"AI Hunt: {params.get('search_text', '')[:50]}",
            "size": 10000,
            "search": params.get("search_text", ""),
            "searchType": params.get("search_type", "ascii"),
            "type": params.get("src_or_dst", "either"),
            "src": params.get("src_or_dst", "either") in ("src", "either"),
            "dst": params.get("src_or_dst", "either") in ("dst", "either"),
        }
        expression = params.get("expression")
        if expression:
            body["query"] = {"expression": expression}
        return ("POST", f"{self._arkime_url}/api/hunt", {"json": body})

    def _build_arkime_hunt_status(self, params):
        return ("GET", f"{self._arkime_url}/api/hunts", {"params": {}})

    def _build_arkime_session_detail(self, params):
        session_id = params.get("session_id", "")
        node = params.get("node", "")
        if node:
            url = f"{self._arkime_url}/api/session/{node}/{session_id}/detail"
        else:
            url = f"{self._arkime_url}/api/session/{session_id}/detail"
        return ("GET", url, {"params": {}})

    def _build_arkime_export_pcap(self, params):
        expression = params.get("expression", "*")
        return ("GET", f"{self._arkime_url}/api/sessions/pcap",
                {"params": {"expression": expression, "date": "-1"}})

    def _build_opensearch_list_fields(self, params):
        index = params.get("index", "arkime_sessions3-*")
        return ("GET", f"{self._opensearch_url}/{index}/_mapping", {"params": {}})

    def _build_opensearch_list_detectors(self, params):
        body = {"query": {"match_all": {}}, "size": 50,
                "_source": ["name", "description", "indices", "feature_attributes"]}
        return ("POST", f"{self._opensearch_url}/_plugins/_anomaly_detection/detectors/_search",
                {"json": body, "headers": {"Content-Type": "application/json"}})

    def _build_opensearch_anomaly_results(self, params):
        detector_id = params.get("detector_id", "")
        start = params.get("start_time", "now-7d")
        end = params.get("end_time", "now")
        body = {"size": 20, "query": {"bool": {"must": [
            {"term": {"detector_id": detector_id}},
            {"range": {"data_start_time": {"gte": start, "lte": end}}},
        ]}}, "sort": [{"anomaly_grade": {"order": "desc"}}]}
        return ("POST", f"{self._opensearch_url}/.opendistro-anomaly-results*/_search",
                {"json": body, "headers": {"Content-Type": "application/json"}})

    def _build_netbox_prefixes(self, params):
        qp = {"limit": 100}
        if params.get("site"):
            qp["site"] = params["site"]
        if params.get("vlan"):
            qp["vlan_id"] = params["vlan"]
        base = self._netbox_url.rstrip("/")
        return ("GET", f"{base}/api/ipam/prefixes/",
                {"params": qp, "headers": {"Accept": "application/json"}})

    def _build_netbox_interfaces(self, params):
        device = params.get("device", "")
        base = self._netbox_url.rstrip("/")
        return ("GET", f"{base}/api/dcim/interfaces/",
                {"params": {"device": device, "limit": 100},
                 "headers": {"Accept": "application/json"}})

    def _build_netbox_lookup(self, params):
        query = params.get("query", "")
        base = self._netbox_url.rstrip("/")
        return ("GET", f"{base}/api/ipam/ip-addresses/",
                {"params": {"q": query},
                 "headers": {"Accept": "application/json"}})

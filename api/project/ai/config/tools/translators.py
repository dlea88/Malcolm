"""Translator functions for converting UI-level tool params to service-level params.

Each function takes the params dict from the LLM's tool selection and returns
a params dict suitable for the service-level tool (MCP server or ToolRegistry).
"""

from ...field_mappings import ecs_filters_to_arkime_expression, ecs_filters_to_lucene_query


def arkime_open(params):
    """Translate arkime:open params to arkime:list_sessions params."""
    return {
        "expression": ecs_filters_to_arkime_expression(params.get("filters", {})),
        "start_time": params.get("time_from", ""),
        "end_time": params.get("time_to", ""),
        "limit": 50,
    }


def dashboard_open(params):
    """Translate opensearch_dashboards:open params to opensearch:search params.

    Note: in navigate mode, _build_action handles dashboard resolution by topic.
    This translator is only used in execute mode where we query OpenSearch directly.
    """
    return {
        "query": ecs_filters_to_lucene_query(params.get("filters", {})),
        "limit": 50,
    }


def dashboard_filter(params):
    """Translate opensearch_dashboards:filter params to opensearch:search params."""
    return {
        "query": ecs_filters_to_lucene_query(params.get("filters", {})),
        "limit": 50,
    }


def netbox_search(params):
    """Translate netbox:search params to netbox:lookup_device params."""
    return {
        "query": params.get("query", ""),
    }


def arkime_export(params):
    """Translate arkime:export params to arkime:export_pcap params."""
    return {
        "expression": params.get("expression", ""),
        "start_time": params.get("time_from", ""),
        "end_time": params.get("time_to", ""),
    }


def strelka_open(params):
    """Translate strelka:open params to opensearch:search for file results."""
    return {
        "query": ecs_filters_to_lucene_query(params.get("filters", {})),
        "index": "arkime_sessions3-*",
        "limit": 50,
    }


# Registry of translator functions by name (referenced from manifest.yml)
TRANSLATORS = {
    "arkime_open": arkime_open,
    "dashboard_open": dashboard_open,
    "dashboard_filter": dashboard_filter,
    "netbox_search": netbox_search,
    "arkime_export": arkime_export,
    "strelka_open": strelka_open,
}

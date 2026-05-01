"""Context merging utilities for combining UI state with LLM output.

When an analyst has existing filters/time on their surface, the LLM's
output should merge with that context rather than replacing it.
"""


def merge_filters(existing, new):
    """Merge two filter dicts, accumulating values for the same key into lists.

    When both dicts contain the same key the values are combined into a list
    so that adapters can decide AND vs OR semantics downstream.

    >>> merge_filters({"source.ip": "10.0.0.1"}, {"source.ip": "10.0.0.2"})
    {'source.ip': ['10.0.0.1', '10.0.0.2']}
    >>> merge_filters({"source.ip": "10.0.0.1"}, {"network.protocol": "dns"})
    {'source.ip': '10.0.0.1', 'network.protocol': 'dns'}
    """
    if not existing:
        return dict(new) if new else {}
    merged = {}
    all_keys = set(existing) | set(new or {})
    for key in all_keys:
        old_val = existing.get(key)
        new_val = (new or {}).get(key)
        if old_val is not None and new_val is not None and old_val != new_val:
            old_list = old_val if isinstance(old_val, list) else [old_val]
            new_list = new_val if isinstance(new_val, list) else [new_val]
            merged[key] = old_list + new_list
        elif new_val is not None:
            merged[key] = new_val
        else:
            merged[key] = old_val
    return merged


def inherit_time_range(ui_time_from, ui_time_to, llm_time_from, llm_time_to):
    """Use LLM time range if provided, otherwise inherit from UI context.

    Returns (time_from, time_to) tuple.
    """
    return (
        llm_time_from or ui_time_from or "",
        llm_time_to or ui_time_to or "",
    )

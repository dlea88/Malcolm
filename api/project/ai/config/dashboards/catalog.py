"""Dashboard catalog — auto-discovers dashboard definitions from *.yml files.

Each YAML file in this directory that contains a ``dashboards:`` key is loaded
and merged. Files are keyed by OpenSearch UUID. The catalog provides search,
URL generation, and semantic profiles for LLM context.
"""

import glob
import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)

_SAFE_FIELD_RE = re.compile(r'^[a-zA-Z0-9_.]+$')
_YAML_DIR = os.path.dirname(__file__)


def _load_dashboard_file(filepath, entries, profiles, low_priority_ids):
    try:
        with open(filepath, 'r') as f:
            data = yaml.safe_load(f)
        if not data or 'dashboards' not in data:
            return
        for uuid, entry in data['dashboards'].items():
            uuid = str(uuid)
            entries[uuid] = {
                "title": entry["title"],
                "keywords": entry.get("keywords", []),
            }
            if entry.get("low_priority"):
                low_priority_ids.add(uuid)
            if "profile" in entry:
                profiles[uuid] = entry["profile"]
        logger.debug("Loaded %d dashboards from %s", len(data['dashboards']), os.path.basename(filepath))
    except Exception:
        logger.exception("Failed to load dashboard file: %s", filepath)


def _discover_dashboards(directory=None):
    directory = directory or _YAML_DIR
    entries = {}
    profiles = {}
    low_priority_ids = set()
    for path in sorted(glob.glob(os.path.join(directory, '*.yml'))):
        _load_dashboard_file(path, entries, profiles, low_priority_ids)
    return entries, profiles, low_priority_ids


class DashboardCatalog:
    """Searchable catalog of Malcolm dashboards, loaded from YAML plugins."""

    def __init__(self, directory=None):
        entries, profiles, low_priority = _discover_dashboards(directory=directory)
        self._entries = entries
        self._profiles = profiles
        self._low_priority_ids = low_priority

    def __len__(self):
        return len(self._entries)

    def exists(self, uuid):
        return uuid in self._entries

    def get(self, uuid):
        return self._entries.get(uuid)

    def search(self, query):
        query_lower = query.lower().strip()
        results = []
        for uuid, entry in self._entries.items():
            title_lower = entry["title"].lower()
            keywords = entry.get("keywords", [])

            if query_lower == title_lower:
                results.append({"id": uuid, "title": entry["title"], "score": 100})
                continue
            if query_lower in title_lower:
                results.append({"id": uuid, "title": entry["title"], "score": 80})
                continue
            for kw in keywords:
                if query_lower == kw or query_lower in kw or kw in query_lower:
                    score = 60 if uuid not in self._low_priority_ids else 20
                    results.append({"id": uuid, "title": entry["title"], "score": score})
                    break

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def dashboard_url(self, uuid, from_time=None, to_time=None):
        if not self.exists(uuid):
            return None
        from_str = f"'{from_time}'" if from_time else "'now-1d'"
        to_str = f"'{to_time}'" if to_time else "'now'"
        return (
            f"/dashboards/app/dashboards#/view/{uuid}"
            f"?_g=(filters:!(),refreshInterval:(pause:!t,value:0),"
            f"time:(from:{from_str},to:{to_str}))"
        )

    def dashboard_url_with_filters(self, uuid, filters=None, from_time=None, to_time=None):
        base_url = self.dashboard_url(uuid, from_time, to_time)
        if not base_url or not filters:
            return base_url

        filter_parts = []
        for field, value in filters.items():
            if not _SAFE_FIELD_RE.match(field):
                continue
            if isinstance(value, list):
                value = value[0] if len(value) == 1 else ", ".join(str(v) for v in value)
            escaped_val = str(value).replace("\\", "\\\\").replace("'", "\\'").replace("(", "\\(").replace(")", "\\)")
            filter_parts.append(
                f"(query:(match_phrase:({field}:'{escaped_val}')))"
            )
        if filter_parts:
            filter_str = ",".join(filter_parts)
            base_url = base_url.replace("filters:!()", f"filters:!({filter_str})")
        return base_url

    def resolve_by_title(self, title, fallback_uuid=None):
        title_lower = title.lower()
        for uuid, entry in self._entries.items():
            if entry["title"].lower() == title_lower:
                return uuid
        if fallback_uuid:
            logger.warning("Dashboard '%s' not found, using fallback UUID %s", title, fallback_uuid)
        return fallback_uuid

    def summary(self):
        return [
            {"id": uuid, "title": entry["title"], "keywords": entry.get("keywords", [])}
            for uuid, entry in self._entries.items()
            if uuid not in self._low_priority_ids
        ]

    def get_profile(self, dashboard_id):
        return self._profiles.get(dashboard_id)

    def get_field_hints(self, dashboard_id):
        profile = self.get_profile(dashboard_id)
        return profile["field_hints"] if profile else ""

    def get_primary_fields(self, dashboard_id):
        profile = self.get_profile(dashboard_id)
        return profile["primary_fields"] if profile else []

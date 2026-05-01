"""Tool manifest — single source of truth for UI-level tool definitions.

Loads manifest.yml and provides methods used by SafetyGate (param schemas),
routes.py (tool translation), and prompts.py (tool names).

Adding a tool: add an entry in manifest.yml. Everything else derives from it.
"""

import logging
import os

import yaml

from .translators import TRANSLATORS

logger = logging.getLogger(__name__)

_MANIFEST_FILE = os.path.join(os.path.dirname(__file__), 'manifest.yml')


class ToolManifest:
    """Loads the tool manifest and provides schema/translation lookups."""

    def __init__(self, manifest_file=None):
        self._tools = {}
        self._load(manifest_file or _MANIFEST_FILE)

    def _load(self, path):
        with open(path, 'r') as f:
            raw = yaml.safe_load(f) or {}

        tools = raw.get('tools', {})
        if not tools:
            raise ValueError(f"Tool manifest has no tools defined: {path}")

        for name, defn in tools.items():
            # Validate translator references at load time
            translator_name = defn.get("translator")
            if translator_name and translator_name not in TRANSLATORS:
                logger.warning(
                    "Tool '%s' references unknown translator '%s' — "
                    "params will pass through untranslated", name, translator_name,
                )
            self._tools[name] = defn
            logger.debug("Loaded tool: %s -> %s", name, defn.get('service_tool'))

    def get_param_schema(self, tool_name):
        """Return the param schema dict for SafetyGate step 2.

        Returns the same shape as the old _TOOL_PARAM_SCHEMAS entries:
        {"description": ..., "schema": {...}, "example": ...}
        """
        defn = self._tools.get(tool_name)
        if not defn:
            return {}
        return {
            "description": defn.get("description", tool_name),
            "schema": defn.get("params", {}),
            "example": defn.get("example", "{}"),
        }

    def get_all_schemas(self):
        """Return all param schemas keyed by tool name.

        Drop-in replacement for the old _TOOL_PARAM_SCHEMAS dict.
        """
        return {name: self.get_param_schema(name) for name in self._tools}

    def resolve(self, nav_tool, params):
        """Translate a UI-level tool + params to a service-level tool + params.

        Returns (service_tool_name, translated_params).
        If no mapping exists, returns the tool name and params unchanged
        (allows service-level tool names to pass through).
        """
        defn = self._tools.get(nav_tool)
        if not defn:
            logger.debug("Tool '%s' not in manifest, passing through", nav_tool)
            return nav_tool, params

        service_tool = defn.get("service_tool")
        if not service_tool:
            return nav_tool, params

        translator_name = defn.get("translator")
        if translator_name and translator_name in TRANSLATORS:
            translated = TRANSLATORS[translator_name](params)
        else:
            translated = params

        return service_tool, translated

    def tool_names(self):
        """Return all UI-level tool names (for prompt building)."""
        return list(self._tools.keys())

    def has_tool(self, name):
        return name in self._tools

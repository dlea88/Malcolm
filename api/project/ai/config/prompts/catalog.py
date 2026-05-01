"""PromptCatalog — discovers and loads per-adapter/per-action prompt hints.

Hint files live in prompts/<adapter>/<action>.yml and contain field_guide
text and few_shot examples that get injected into the system prompt.
"""

import glob
import logging
import os

import yaml

logger = logging.getLogger(__name__)

_YAML_DIR = os.path.dirname(__file__)


class PromptCatalog:

    def __init__(self, directory=None):
        self._hints = {}  # (adapter, action) -> {description, field_guide, few_shot}
        self._discover(directory or _YAML_DIR)

    def _discover(self, directory):
        pattern = os.path.join(directory, '*', '*.yml')
        for path in sorted(glob.glob(pattern)):
            adapter = os.path.basename(os.path.dirname(path))
            action = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                self._hints[(adapter, action)] = data
                logger.debug("prompt hint: %s:%s (%d examples)", adapter, action, len(data.get('few_shot', [])))
            except Exception:
                logger.warning("failed to load prompt hint: %s", path)

    def get_hints(self, adapter, action):
        return self._hints.get((adapter, action))

    def format_for_prompt(self, adapter, action):
        hints = self.get_hints(adapter, action)
        if not hints:
            return ""
        lines = []
        tool_name = f"{adapter}:{action}"
        if hints.get('field_guide'):
            lines.append(hints['field_guide'].strip())
        few_shot = hints.get('few_shot', [])
        if few_shot:
            lines.append(f"\nCorrect usage examples for {tool_name}:")
            for ex in few_shot:
                params_str = ", ".join(f"{k}: {v}" for k, v in ex.get('params', {}).items())
                lines.append(f'  User: "{ex["user"]}"')
                lines.append(f"  -> {tool_name} with {params_str}")
        return "\n".join(lines)

    def format_multi(self, adapter, actions=None):
        blocks = []
        for (a, act), _hints in self._hints.items():
            if a != adapter:
                continue
            if actions and act not in actions:
                continue
            block = self.format_for_prompt(a, act)
            if block:
                blocks.append(block)
        return "\n\n".join(blocks)

    def __len__(self):
        return len(self._hints)

"""Command catalog — auto-discovers command definitions from YAML files.

Each *.yml file in this directory defines one command: slash shortcut,
description, tool mapping, LLM descriptions. Files are keyed by
filename stem (e.g. filter.yml -> "filter").

Commands are verb hints for the LLM, not execution definitions. Model
overrides belong on the tool manifest, not here.
"""

import glob
import logging
import os

import yaml

logger = logging.getLogger(__name__)

_PLUGIN_DIR = os.path.dirname(__file__)


def _discover(directory=None):
    directory = directory or _PLUGIN_DIR
    commands = {}
    for path in sorted(glob.glob(os.path.join(directory, '*.yml'))):
        try:
            with open(path, 'r') as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            logger.exception("Failed to load command file: %s", path)
            continue
        key = os.path.splitext(os.path.basename(path))[0]
        commands[key] = data
    return commands


class CommandCatalog:
    """Provides indexed views over the command definitions."""

    def __init__(self, directory=None):
        self._commands = _discover(directory)
        self._build_indexes()

    def _build_indexes(self):
        self._slash_commands = []
        for cmd in self._commands.values():
            if 'slash' not in cmd:
                continue
            self._slash_commands.append({
                'name': cmd['slash'],
                'description': cmd['description'],
                'hint': cmd.get('hint', ''),
                'examples': cmd.get('examples', []),
            })

        self._verb_constraints = {}
        for cmd in self._commands.values():
            if 'slash' not in cmd:
                continue
            verb = cmd['slash']
            if 'tool' in cmd:
                self._verb_constraints[verb] = [cmd['tool']]
            elif 'tools' in cmd:
                self._verb_constraints[verb] = list(cmd['tools'])

        self._tool_descriptions = {}
        for cmd in self._commands.values():
            llm = cmd.get('llm')
            if not llm:
                continue
            tool = cmd.get('tool')
            if tool:
                self._tool_descriptions[tool] = dict(llm)
            elif cmd.get('tools'):
                for tool_name, descs in llm.items():
                    if isinstance(descs, dict):
                        self._tool_descriptions[tool_name] = dict(descs)
                    else:
                        self._tool_descriptions[tool_name] = {'default': descs}

        self._verb_guides = {}
        for cmd in self._commands.values():
            if 'slash' in cmd and 'verb_guide' in cmd:
                self._verb_guides[cmd['slash']] = cmd['verb_guide']

    def slash_commands(self):
        return list(self._slash_commands)

    def verb_constraints(self):
        return dict(self._verb_constraints)

    def verb_guide(self, verb):
        return self._verb_guides.get(verb, '')

    def tool_description(self, tool_name, surface=None):
        descs = self._tool_descriptions.get(tool_name, {})
        if surface and surface in descs:
            return descs[surface]
        if surface and 'context' in descs:
            return descs['context']
        return descs.get('default', '')

    def build_tool_list(self, tool_names, current_tool=None):
        lines = []
        for i, name in enumerate(tool_names, 1):
            desc = self.tool_description(name, current_tool)
            if desc:
                lines.append(f"{i}. {desc}")
        return "\n\n".join(lines)

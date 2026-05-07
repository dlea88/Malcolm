"""Workflow registry — auto-discovers workflow definitions from YAML files.

Each *.yml file in config/workflows/ defines one workflow. The filename
(without extension) becomes the workflow ID.

Each workflow defines a sequence of goals the agent pursues. Each step
constrains which tools the LLM can use and provides explicit instructions
with {prev.field} substitution for data from earlier steps.
"""

import glob
import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "config", "workflows"
)


class WorkflowDef:
    """A loaded workflow definition."""

    def __init__(self, workflow_id, data):
        self.id = workflow_id
        self.name = data.get("name", workflow_id)
        self.description = data.get("description", "")
        self.triggers = data.get("triggers", [])
        self.max_steps = data.get("max_steps", 5)
        self.model = data.get("model", "")
        self.investigation = data.get("investigation", False)
        self.steps = data.get("steps", [])
        self.params = data.get("params", {})

    def get_steps(self, user_params=None):
        """Return step definitions with user params substituted."""
        if not user_params:
            return self.steps

        steps = []
        for step in self.steps:
            s = dict(step)
            prompt = s.get("prompt", "")
            for key, value in user_params.items():
                prompt = prompt.replace(f"{{{key}}}", str(value))
            prompt = re.sub(r'\{(\w+)\}', '', prompt)
            s["prompt"] = prompt.strip()
            steps.append(s)
        return steps


class WorkflowRegistry:
    """Registry of available workflows, auto-discovered from YAML files."""

    def __init__(self, directory=None):
        self._workflows: dict[str, WorkflowDef] = {}
        self._discover(directory or _WORKFLOWS_DIR)

    def _discover(self, directory):
        if not os.path.isdir(directory):
            logger.warning("Workflows directory not found: %s", directory)
            return

        for path in sorted(glob.glob(os.path.join(directory, "*.yml"))):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue
                workflow_id = os.path.splitext(os.path.basename(path))[0]
                self._workflows[workflow_id] = WorkflowDef(workflow_id, data)
            except Exception:
                logger.exception("Failed to load workflow: %s", path)

        logger.info("Loaded %d workflows from %s", len(self._workflows), directory)

    def match(self, query: str) -> WorkflowDef | None:
        """Match a query to a workflow using keyword triggers."""
        query_lower = query.lower()
        best = None
        best_score = 0

        for wf in self._workflows.values():
            score = sum(1 for t in wf.triggers if t.lower() in query_lower)
            if score > best_score:
                best_score = score
                best = wf

        return best

    def get(self, workflow_id: str) -> WorkflowDef | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> list[dict]:
        return [
            {"id": wf.id, "name": wf.name, "description": wf.description}
            for wf in self._workflows.values()
        ]

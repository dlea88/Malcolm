"""Model catalog — auto-discovers model definitions from *.yml files.

Each *.yml file in this directory defines one model endpoint: provider,
connection details, model name, timeout, max tokens.

Environment variable interpolation:
  Values like ${OLLAMA_URL:-http://ollama:11434} are resolved at load time.
  ${VAR} uses the env var (empty string if unset).
  ${VAR:-default} uses the env var or the default if unset/empty.
"""

import glob
import logging
import os

import yaml

from ..loader import resolve_env_recursive

logger = logging.getLogger(__name__)

_YAML_DIR = os.path.dirname(__file__)


class ModelDef:
    """A loaded model definition."""

    __slots__ = ('name', 'description', 'provider', 'connection', 'model',
                 'max_tokens', 'structured_output', 'optional', 'raw')

    def __init__(self, data):
        self.raw = data
        self.name = data.get('name', 'unnamed')
        self.description = data.get('description', '')
        self.provider = data.get('provider', 'ollama')
        self.connection = data.get('connection', {})
        self.model = data.get('model', '')
        self.max_tokens = int(data.get('max_tokens', 512))
        self.structured_output = data.get('structured_output', 'json')
        self.optional = data.get('optional', False)

    @property
    def url(self):
        return self.connection.get('url', '')

    @property
    def api_key(self):
        """Resolve API key: either a direct value or read from an env var name."""
        key_env = self.connection.get('api_key_env')
        if key_env:
            return os.environ.get(key_env, '')
        return self.connection.get('api_key', '')

    @property
    def timeout(self):
        try:
            return int(self.connection.get('timeout', 30))
        except (ValueError, TypeError):
            return 30

    def is_available(self):
        """Check if this model has enough config to attempt a call."""
        if self.provider == 'ollama':
            return bool(self.url and self.model)
        if self.provider in ('openai_compatible', 'vllm'):
            return bool(self.url and self.api_key and self.model)
        if self.provider == 'anthropic':
            return bool(self.api_key and self.model)
        return False

    def __repr__(self):
        return f"ModelDef(name={self.name!r}, provider={self.provider!r}, model={self.model!r})"


class ModelCatalog:
    """Auto-discovers model definitions from *.yml files in the models directory."""

    def __init__(self, default_model='llama-3_2-3b', directory=None):
        self._models = {}
        self._default_model = default_model
        self._discover(directory or _YAML_DIR)

    def _discover(self, directory):
        for path in sorted(glob.glob(os.path.join(directory, '*.yml'))):
            try:
                with open(path, 'r') as f:
                    raw = yaml.safe_load(f) or {}
            except Exception:
                logger.exception("Failed to load model file: %s", path)
                continue
            if 'name' not in raw:
                continue
            resolved = resolve_env_recursive(raw)
            model_def = ModelDef(resolved)
            self._models[model_def.name] = model_def
            logger.debug("Loaded model: %s (%s)", model_def.name, path)

    def get(self, name):
        return self._models.get(name)

    def get_available(self, name):
        model = self._models.get(name)
        if model and model.is_available():
            return model
        return None

    def all_models(self):
        return dict(self._models)

    def available_models(self):
        return {k: v for k, v in self._models.items() if v.is_available()}

    @property
    def default_model(self):
        return self._default_model

    def summary(self):
        result = {}
        for name, model in self._models.items():
            result[name] = {
                'provider': model.provider,
                'model': model.model,
                'available': model.is_available(),
                'optional': model.optional,
            }
        return result

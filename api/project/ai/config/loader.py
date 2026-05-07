"""Config loader with environment variable interpolation.

Values like ${OLLAMA_URL:-http://ollama:11434} are resolved at load time.
${VAR} uses the env var (empty string if unset).
${VAR:-default} uses the env var or the default if unset/empty.
"""

import os
import re

import yaml

from .settings import AISettings

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'ai.yml')
_ENV_RE = re.compile(r'\$\{([^}]+)\}')


def resolve_env(value):
    """Resolve ${VAR} and ${VAR:-default} patterns in a string value."""
    if not isinstance(value, str):
        return value

    def _replace(match):
        expr = match.group(1)
        if ':-' in expr:
            var, default = expr.split(':-', 1)
            return os.environ.get(var, default)
        return os.environ.get(expr, '')

    return _ENV_RE.sub(_replace, value)


def resolve_env_recursive(obj):
    """Walk a dict/list and resolve env vars in all string values."""
    if isinstance(obj, dict):
        return {k: resolve_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_recursive(v) for v in obj]
    if isinstance(obj, str):
        return resolve_env(obj)
    return obj


def load_config(config_file=None):
    """Load and return AISettings from ai.yml."""
    config_file = config_file or _CONFIG_FILE
    with open(config_file, 'r') as f:
        raw = yaml.safe_load(f) or {}
    resolved = resolve_env_recursive(raw)
    return AISettings(resolved)

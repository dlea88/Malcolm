"""Centralized service connection config for Malcolm backends.

All AI subsystem modules that talk to OpenSearch, Arkime, NetBox, or
the Malcolm API should import from here instead of reading env vars directly.
"""

import os

# --- AI Engine Config ---
def _str2bool(val):
    return str(val).lower() in ("true", "1", "yes", "on")

AI_EXECUTE_ENABLED = _str2bool(os.environ.get("AI_EXECUTE_ENABLED", "false"))

# --- OpenSearch ---
OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "https://opensearch:9200")
OPENSEARCH_VERIFY_SSL = os.environ.get(
    "OPENSEARCH_SSL_CERTIFICATE_VERIFICATION", "false"
).lower() not in ("false", "0", "no", "")
_os_user = os.environ.get("OPENSEARCH_USER", "admin")
_os_pass = os.environ.get("OPENSEARCH_PASSWORD", "admin")
OPENSEARCH_AUTH = (_os_user, _os_pass) if _os_user else None

# --- Arkime ---
ARKIME_URL = os.environ.get("ARKIME_URL", "https://arkime:8005")
ARKIME_VERIFY_SSL = os.environ.get(
    "ARKIME_SSL_CERTIFICATE_VERIFICATION", "false"
).lower() not in ("false", "0", "no", "")
_ark_user = os.environ.get("ARKIME_USER", os.environ.get("MALCOLM_API_USER", ""))
_ark_pass = os.environ.get("ARKIME_PASSWORD", os.environ.get("MALCOLM_API_PASSWORD", ""))
ARKIME_AUTH = (_ark_user, _ark_pass) if _ark_user else None

# --- Malcolm API ---
MALCOLM_API_URL = os.environ.get("MALCOLM_API_URL", "http://api:5000")
MALCOLM_API_PREFIX = os.environ.get("MALCOLM_API_PREFIX", "mapi")
MALCOLM_API_VERIFY_SSL = _str2bool(os.environ.get("MALCOLM_API_VERIFY_SSL", "false"))
_api_user = os.environ.get("MALCOLM_API_USER", "")
_api_pass = os.environ.get("MALCOLM_API_PASSWORD", "")
MALCOLM_API_AUTH = (_api_user, _api_pass) if _api_user else None

# --- NetBox ---
NETBOX_URL = os.environ.get("NETBOX_URL", "http://netbox:8080/netbox")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")
_nb_user = os.environ.get("NETBOX_USER", os.environ.get("MALCOLM_API_USER", ""))
_nb_pass = os.environ.get("NETBOX_PASSWORD", os.environ.get("MALCOLM_API_PASSWORD", ""))
NETBOX_AUTH = (_nb_user, _nb_pass) if _nb_user else None

# --- Ollama ---
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "llama3.1:8b")

#!/usr/bin/env python3
"""Start Malcolm MCP servers locally for testing.

Runs each service MCP on its configured port. Connects to Malcolm
services via the nginx proxy on localhost:443.

Usage:
    # Start all MCP servers:
    python -m api.project.ai.mcp.run_servers

    # Start specific servers:
    python -m api.project.ai.mcp.run_servers arkime malcolm-api

    # List available servers:
    python -m api.project.ai.mcp.run_servers --list
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("mcp-runner")

# When running outside Docker, point at the nginx proxy on localhost.
# Inside Docker these would resolve via container hostnames.
DEFAULTS = {
    "ARKIME_URL": "https://localhost:443/arkime",
    "ARKIME_VERIFY_SSL": "false",
    "OPENSEARCH_URL": "https://localhost:9200",
    "OPENSEARCH_SSL_CERTIFICATE_VERIFICATION": "false",
    "MALCOLM_API_URL": "https://localhost:443",
    "MALCOLM_API_PREFIX": "mapi",
    "NETBOX_URL": "https://localhost:443/netbox",
}

# Auth defaults (loaded from ai.env, or set manually)
AUTH_DEFAULTS = {
    "OPENSEARCH_USER": "",
    "OPENSEARCH_PASSWORD": "",
    "MALCOLM_API_USER": "",
    "MALCOLM_API_PASSWORD": "",
    "MALCOLM_API_VERIFY_SSL": "false",
}


def _load_env():
    """Set env vars from ai.env and defaults for local testing."""
    # Load ai.env if it exists
    ai_env_path = os.path.join(os.path.dirname(__file__), "../../../../config/ai.env")
    ai_env_path = os.path.normpath(ai_env_path)
    if os.path.exists(ai_env_path):
        logger.info("Loading %s", ai_env_path)
        with open(ai_env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

    # Apply defaults for local dev (don't override existing env vars)
    for key, val in {**DEFAULTS, **AUTH_DEFAULTS}.items():
        os.environ.setdefault(key, val)


# Server registry: name -> (module, default_port)
# Module paths are relative to PYTHONPATH (api/project when running locally).
SERVERS = {
    "malcolm": ("ai.mcp.malcolm_server", 8085),
    "malcolm-api": ("ai.mcp.malcolm_api_server", 8088),
    # Service MCPs (Arkime, OpenSearch, NetBox) are external — see docs/adr/007
}


def _start_server(name, module_path, port):
    """Import and start a single MCP server via uvicorn in a thread."""
    import importlib
    import threading
    import uvicorn

    mod = importlib.import_module(module_path)
    app = mod.mcp.streamable_http_app()
    logger.info("Starting %s MCP on port %d (http://localhost:%d/mcp)", name, port, port)

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return thread


def run_all(names):
    """Start MCP servers in threads, block until interrupted."""
    threads = []
    for name in names:
        module_path, default_port = SERVERS[name]
        port_env = f"AI_MCP_{name.upper().replace('-', '_')}_PORT"
        port = int(os.environ.get(port_env, default_port))
        threads.append(_start_server(name, module_path, port))

    logger.info("All servers started. Press Ctrl+C to stop.")
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        logger.info("Shutting down.")


def main():
    _load_env()

    args = sys.argv[1:]

    if "--list" in args:
        print("Available MCP servers:")
        for name, (mod, port) in SERVERS.items():
            print(f"  {name:15s}  port {port}  ({mod})")
        return

    if args:
        names = [a for a in args if a in SERVERS]
        unknown = [a for a in args if a not in SERVERS and not a.startswith("-")]
        if unknown:
            logger.error("Unknown servers: %s", ", ".join(unknown))
            logger.info("Available: %s", ", ".join(SERVERS.keys()))
            sys.exit(1)
    else:
        names = list(SERVERS.keys())

    logger.info("Starting MCP servers: %s", ", ".join(names))
    run_all(names)


if __name__ == "__main__":
    main()

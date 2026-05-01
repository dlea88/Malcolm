"""Analyst widget test suite — designed to run via Playwright MCP.

This file documents test cases and expected results. Each test is a
function that returns a dict describing what to do and what to check.
The actual execution happens via Playwright MCP tool calls.

Test categories:
  1. Widget Loading — does the widget appear on each surface?
  2. Panel Interaction — open, close, collapse, resize, drag
  3. Query Handling — submit queries, verify responses and navigation
  4. Slash Commands — autocomplete popup, command selection
  5. Chat History — persistence, clear, export
  6. Error Handling — bad queries, server errors, abort
  7. Cross-Surface — context scraping from Arkime, Dashboards, NetBox
  8. Auto-Navigation — correct URL construction and redirect
"""

SURFACES = {
    "landing": "https://localhost/",
    "dashboards": "https://localhost/dashboards/",
    "arkime": "https://localhost/arkime/",
}

TESTS = [
    # === 1. Widget Loading ===
    {
        "id": "load-landing",
        "category": "Widget Loading",
        "description": "Widget button appears on Malcolm landing page",
        "steps": [
            "Navigate to https://localhost/",
            "Wait 2s for widget boot",
            "Snapshot page",
        ],
        "expected": "Malcolm wheel button visible with title 'Ask Malcolm'",
    },
    {
        "id": "load-dashboards",
        "category": "Widget Loading",
        "description": "Widget button appears on OpenSearch Dashboards",
        "steps": [
            "Navigate to https://localhost/dashboards/",
            "Wait 3s for SPA + widget boot",
            "Snapshot page",
        ],
        "expected": "Malcolm wheel button visible (MutationObserver re-attaches after SPA render)",
    },
    {
        "id": "load-arkime",
        "category": "Widget Loading",
        "description": "Widget button appears on Arkime",
        "steps": [
            "Navigate to https://localhost/arkime/",
            "Wait 2s for widget boot",
            "Snapshot page",
        ],
        "expected": "Malcolm wheel button visible",
    },

    # === 2. Panel Interaction ===
    {
        "id": "panel-open",
        "category": "Panel Interaction",
        "description": "Clicking button opens chat panel",
        "steps": [
            "Navigate to landing page",
            "Click the Ask Malcolm button",
            "Snapshot page",
        ],
        "expected": "Panel visible with header 'Ask Malcolm', input field, Ask button",
    },
    {
        "id": "panel-close",
        "category": "Panel Interaction",
        "description": "Close button hides panel",
        "steps": [
            "Open panel",
            "Click close button (x)",
            "Snapshot page",
        ],
        "expected": "Panel hidden, button still visible",
    },
    {
        "id": "panel-collapse",
        "category": "Panel Interaction",
        "description": "Collapse button minimizes chat log",
        "steps": [
            "Open panel",
            "Click collapse button",
            "Snapshot page",
        ],
        "expected": "Chat log hidden, header and input still visible",
    },

    # === 3. Query Handling ===
    {
        "id": "query-dns",
        "category": "Query Handling",
        "description": "Ask for DNS traffic navigates to DNS dashboard",
        "steps": [
            "Open panel on landing page",
            "Type 'show me DNS traffic'",
            "Click Ask",
            "Wait 8s for LLM response",
            "Snapshot page",
        ],
        "expected": "Page navigated to DNS dashboard URL containing view/2cf94cd0",
    },
    {
        "id": "query-ip",
        "category": "Query Handling",
        "description": "Ask about IP navigates to Arkime",
        "steps": [
            "Open panel",
            "Type 'show traffic from 10.200.1.252'",
            "Click Ask",
            "Wait 8s",
            "Snapshot page",
        ],
        "expected": "Page navigated to Arkime sessions URL with expression containing ip.src",
    },
    {
        "id": "query-netbox",
        "category": "Query Handling",
        "description": "Asset lookup routes to NetBox",
        "steps": [
            "Open panel",
            "Type 'who owns 10.50.1.10'",
            "Click Ask",
            "Wait 8s",
            "Snapshot page",
        ],
        "expected": "Response shows netbox:search tool selected",
    },
    {
        "id": "query-adversarial",
        "category": "Query Handling",
        "description": "Off-topic query returns unknown",
        "steps": [
            "Open panel",
            "Type 'write me a poem'",
            "Click Ask",
            "Wait 5s",
            "Snapshot page",
        ],
        "expected": "Response shows 'unknown' tool, no navigation",
    },
    {
        "id": "query-ssh-dashboard",
        "category": "Query Handling",
        "description": "Dashboard by protocol name",
        "steps": [
            "Open panel",
            "Type 'show the SSH dashboard'",
            "Click Ask",
            "Wait 8s",
            "Snapshot page",
        ],
        "expected": "Page navigated to SSH dashboard",
    },

    # === 4. Slash Commands ===
    {
        "id": "slash-popup",
        "category": "Slash Commands",
        "description": "Typing / shows command autocomplete popup",
        "steps": [
            "Open panel",
            "Type '/' in input field",
            "Snapshot page",
        ],
        "expected": "Command popup visible with available commands (open, search, filter, etc.)",
    },

    # === 5. Chat History ===
    {
        "id": "history-persist",
        "category": "Chat History",
        "description": "Chat history persists across page navigation",
        "steps": [
            "Open panel, submit a query, wait for response",
            "Navigate to a different page",
            "Open panel again",
            "Snapshot page",
        ],
        "expected": "Previous messages visible in chat log",
    },
    {
        "id": "history-clear",
        "category": "Chat History",
        "description": "Clear button removes chat history",
        "steps": [
            "Open panel with existing history",
            "Click clear button (trash icon)",
            "Snapshot page",
        ],
        "expected": "Chat log empty",
    },

    # === 6. Error Handling ===
    {
        "id": "error-empty",
        "category": "Error Handling",
        "description": "Empty query is not submitted",
        "steps": [
            "Open panel",
            "Click Ask with empty input",
            "Snapshot page",
        ],
        "expected": "No message added, form validation prevents submit",
    },

    # === 7. Context Scraping ===
    {
        "id": "context-dashboards",
        "category": "Context Scraping",
        "description": "Widget scrapes dashboard UUID and time from URL",
        "steps": [
            "Navigate to a specific dashboard URL with time range",
            "Open panel",
            "Submit query 'filter by source.ip 10.0.0.1'",
            "Wait 8s",
            "Check response includes dashboard context",
        ],
        "expected": "Request sent with current_tool=opensearch_dashboards and dashboard_id",
    },
]

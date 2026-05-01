#!/usr/bin/env python3
"""Integration test suite for Malcolm AI.

Requires: Malcolm running, MCP servers started, Ollama with phi4.

Usage:
    # Start servers first:
    AI_DEFAULT_MODEL=ollama-phi4 python -m ai.mcp.run_servers &

    # Run tests:
    python -m ai.tests.test_integration
"""

import asyncio
import json
import sys

from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession


PASS = 0
FAIL = 0
ERRORS = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \033[32mPASS\033[0m: {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  \033[31mFAIL\033[0m: {name} -- {detail}")


async def test_service_mcps():
    """Test service MCP servers (Arkime, OpenSearch, Malcolm API)."""
    print("\n=== Service MCPs ===")

    # Arkime
    async with streamablehttp_client("http://localhost:8086/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            check("arkime: 4 tools", len(tools.tools) == 4)

            result = await s.call_tool("build_query", {"expression": "protocols == dns"})
            d = json.loads(result.content[0].text)
            check("arkime: build_query valid", d.get("valid"))
            check("arkime: returns sessions_url", "sessions_url" in d)
            check("arkime: returns pcap_url", "pcap_url" in d)

            result = await s.call_tool("list_sessions", {"expression": "protocols == dns", "limit": 2})
            d = json.loads(result.content[0].text)
            check("arkime: list_sessions has data", d.get("total", 0) > 0)

            # Edge case: invalid expression
            result = await s.call_tool("build_query", {"expression": "invalid garbage !@#"})
            d = json.loads(result.content[0].text)
            check("arkime: invalid expression handled", d.get("valid") == False)

    # OpenSearch
    async with streamablehttp_client("http://localhost:8087/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            check("opensearch: 4 tools", len((await s.list_tools()).tools) == 4)

            result = await s.call_tool("aggregate", {"field": "event.dataset", "size": 5})
            d = json.loads(result.content[0].text)
            check("opensearch: aggregate has buckets", len(d.get("buckets", [])) > 0)

    # Malcolm API
    async with streamablehttp_client("http://localhost:8088/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            check("malcolm_api: 5 tools", len((await s.list_tools()).tools) == 5)

            result = await s.call_tool("malcolm_version", {})
            d = json.loads(result.content[0].text)
            check("malcolm_api: version returns opensearch info", "opensearch" in d)


async def test_orchestrator_core():
    """Test Malcolm orchestrator core tools."""
    print("\n=== Orchestrator Core ===")

    async with streamablehttp_client("http://localhost:8085/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            check("orchestrator: 7 tools", len(tools) == 7, f"got {len(tools)}: {tools}")

            # explain_field
            result = await s.call_tool("explain_field", {"field_name": "source.ip"})
            d = json.loads(result.content[0].text)
            check("explain_field: found", d.get("found"))
            check("explain_field: has arkime name", d.get("arkime_name") == "ip.src")


async def test_llm_routing():
    """Test LLM-powered tool selection."""
    print("\n=== LLM Routing (Ollama) ===")

    async with streamablehttp_client("http://localhost:8085/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # DNS on Arkime
            result = await s.call_tool("ask", {"query": "show DNS traffic", "surface": "arkime"})
            d = json.loads(result.content[0].text)
            check("ask: DNS routes to tool", d.get("status") == "resolved")
            check("ask: uses ollama model", "ollama" in d.get("model_used", ""))

            # NetBox routing
            result = await s.call_tool("ask", {"query": "who owns 10.0.0.1"})
            d = json.loads(result.content[0].text)
            check("ask: ownership routes to netbox", d.get("tool") == "netbox:search")


async def test_investigation():
    """Test investigation — now auto-created by workflows."""
    print("\n=== Investigation (via workflow) ===")

    async with streamablehttp_client("http://localhost:8085/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # list_investigations should be available
            result = await s.call_tool("list_investigations", {})
            # Returns a list (may be empty if no workflows run yet)
            check("list_investigations: callable", not result.isError)


async def test_detection():
    """Test detection engineering — placeholder for future Suricata MCP."""
    print("\n=== Detection Engineering ===")
    # generate_rule and generate_zeek_script removed from Malcolm MCP.
    # Rule/script generation will be handled by dedicated service MCPs
    # (Suricata MCP, Zeek MCP) at a later date.
    print("  Skipped — detection tools moved to future service MCPs")


async def test_workflows():
    """Test multi-step workflows."""
    print("\n=== Workflows ===")

    async with streamablehttp_client("http://localhost:8085/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # Weird workflow
            result = await s.call_tool("investigate_weird", {"limit": 2})
            d = json.loads(result.content[0].text)
            check("weird workflow: success", d.get("success"))
            check("weird workflow: found sessions", d.get("context", {}).get("session_count", 0) > 0)
            check("weird workflow: has URLs", "arkime_sessions" in d.get("urls", {}))

            # Alert workflow
            result = await s.call_tool("investigate_alert", {"signature_filter": "DYN_DNS", "limit": 3})
            d = json.loads(result.content[0].text)
            check("alert workflow: success", d.get("success"))
            check("alert workflow: found alerts", d.get("context", {}).get("alert_count", 0) > 0)
            check("alert workflow: has PCAP URL", "pcap_download" in d.get("urls", {}))


async def test_rag():
    """Test RAG knowledge base."""
    print("\n=== RAG Knowledge Base ===")

    async with streamablehttp_client("http://localhost:8085/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            result = await s.call_tool("search_fields", {"query": "DNS query domain"})
            items = [json.loads(c.text) for c in result.content if hasattr(c, "text")]
            check("rag: returns results", len(items) > 0)
            if items:
                check("rag: relevant field", "dns" in items[0].get("field_name", "").lower())


async def main():
    tests = [
        test_service_mcps,
        test_orchestrator_core,
        test_llm_routing,
        test_investigation,
        test_detection,
        test_workflows,
        test_rag,
    ]

    for test in tests:
        try:
            await test()
        except Exception as e:
            global FAIL
            FAIL += 1
            ERRORS.append(f"{test.__name__}: {e}")
            print(f"  \033[31mERROR\033[0m: {test.__name__} crashed: {e}")

    print(f"\n{'='*50}")
    print(f"RESULTS: \033[32m{PASS} passed\033[0m, \033[31m{FAIL} failed\033[0m")
    if ERRORS:
        print("\nFailures:")
        for e in ERRORS:
            print(f"  - {e}")

    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

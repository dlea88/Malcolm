# Malcolm AI Integration: Refined Research — March 2026

## What this document adds over the prior two reports

Both the CLA and OAI reports converge on the right structural insight: Malcolm's architecture is already wired for AI augmentation, the MCP-server-per-service pattern is the correct integration layer, and the LLM should plan and translate while Malcolm's deterministic engines execute. That framing is sound. This document does not revisit it.

Instead, this document addresses five gaps and provides updated findings based on developments through March 2026:

1. **The model landscape has shifted materially** — Foundation-Sec-8B now has a Reasoning variant, and US-origin models like Llama 3.1 8B and Microsoft Phi-4 14B provide strong tool-calling at commodity hardware scale. A dual-model strategy may outperform either alone.
2. **The BYOAPI story needs a concrete architecture** — neither report specifies how the same codebase serves airgapped Ollama, customer-hosted vLLM, and cloud Claude/OpenAI APIs. Mozilla's any-llm gateway layer solves this.
3. **The open-source agent framework landscape has matured** — `langchain-mcp-adapters` with `MultiServerMCPClient` is now production-ready, removing the need for custom agent-to-MCP glue.
4. **MCP security governance is now a real concern** — with SAFE-MCP (Linux Foundation), Cisco DefenseClaw/MCP-Scanner, and emerging MCP gateway products, Malcolm's AI layer needs MCP security baked in from day one.
5. **Several high-ROI features were underspecified** — this document sharpens the top candidates into buildable specifications with clear scope boundaries.

---

## 1. Updated model strategy: dual-model with a reasoning tier

### Foundation-Sec-8B-Reasoning changes the recommendation

Since the prior reports were written, Cisco released **Foundation-Sec-8B-Reasoning** (January 28, 2026), a reasoning-tuned variant that produces explicit chain-of-thought traces before answering. This is purpose-built for multi-step security analysis: threat modeling, attack path analysis, vulnerability root-cause mapping, and configuration review. Cisco deployed it live in their SOC at Cisco Live Amsterdam 2026 through XDR integration, validating it in a real operational environment.

The Reasoning variant matters for Malcolm because the prior reports recommended the base or instruct model primarily as a general assistant. The Reasoning model is the one you actually want for the hard problems — correlating Suricata alerts with Zeek connection logs, tracing lateral movement through Arkime sessions, and assessing whether an anomaly in an ICS environment represents a genuine threat. It produces reasoning traces that are auditable, directly addressing the OAI report's concern about explainability.

However, reasoning models are slow. Foundation-Sec-8B-Reasoning will likely run at 2–5 tokens/second on CPU-only hardware (quantized), which is fine for complex analysis but painful for interactive tool-calling loops where the agent needs to make 5–10 rapid decisions about which MCP tool to invoke next.

### Llama 3.1 8B and Phi-4 14B as orchestration models

For default model recommendations, Malcolm should ship with **US-origin models only**. Users who want to plug in Qwen, Mistral, DeepSeek, or other providers via BYOAPI are free to do so — but the out-of-box defaults should be American AI models for supply chain trust and alignment with Malcolm's government/critical-infrastructure user base.

The two strongest US-origin options for orchestration (tool-calling, query generation, plan execution):

- **Meta Llama 3.1 8B Instruct** (Llama Community License): The workhorse. Proven tool-calling support across Ollama, vLLM, and llama.cpp. Quantized Q4_K_M runs at ~5GB. Strong instruction-following, reliable structured output generation. The same architecture backbone as Foundation-Sec-8B, so the two models share inference infrastructure cleanly. Available on Ollama as `llama3.1:8b`.
- **Microsoft Phi-4 14B** (MIT License): Stronger reasoning than Llama 3.1 8B at the cost of ~2x resource draw. Tool-calling support exists via community templates on Ollama and natively in Phi-4-mini (3.8B). The Phi-4-Reasoning variant rivals much larger models on complex reasoning tasks. On a 64GB system, Phi-4 14B Q4_K_M (~8GB) is a realistic default that gives meaningfully better orchestration quality than an 8B model.
- **Microsoft Phi-4-mini 3.8B** (MIT License): For severely resource-constrained deployments. Native function calling support, 128K context window, runs on hardware as low as 4GB VRAM. Not the default, but a viable fallback for laptop/field deployments.

The recommended default depends on available hardware:

| Hardware tier | Default orchestrator | Default reasoning model |
|---------------|---------------------|------------------------|
| Minimal (32GB RAM, no GPU) | Llama 3.1 8B Q4 | Foundation-Sec-8B-Reasoning Q4 (swapped in on demand) |
| Recommended (64GB RAM, no GPU) | Phi-4 14B Q4 | Foundation-Sec-8B-Reasoning Q4 (co-resident) |
| Optimal (64GB RAM + GPU) | Phi-4 14B Q4 on GPU | Foundation-Sec-8B-Reasoning Q4 on CPU |

### The dual-model recommendation

Run **two models** in the Ollama container, selected per-task:

| Task type | Model | Why |
|-----------|-------|-----|
| Tool-calling orchestration, query generation, plan execution | Llama 3.1 8B or Phi-4 14B | Fast, reliable structured output, good function-calling compliance |
| Security reasoning, alert triage narrative, threat assessment, rule review | Foundation-Sec-8B-Reasoning | Deep security domain knowledge, auditable reasoning traces |
| Lightweight tasks (field explanation, doc lookup, syntax help) | Llama 3.1 8B | Fast, low resource draw |

This dual-model approach is practical because Ollama supports multiple loaded models with automatic memory management, and quantized models coexist comfortably. The orchestrator routes based on task type, not user choice — the analyst asks a question, and the system selects the appropriate model.

For BYOAPI deployments where the user has API access to Claude or GPT, the orchestration model is replaced by the cloud API (which will be better at tool-calling than any local 8B/14B), while Foundation-Sec-8B-Reasoning may still run locally for sensitive analysis that shouldn't leave the network. Users who prefer non-US models like Qwen3 or Mistral for orchestration can configure them freely — Malcolm's architecture is model-agnostic by design.

### Model update and distribution

Both model families are available as GGUF quantized files. The airgapped transfer procedure is identical to what both reports describe: pull on an internet-connected staging machine, export the Ollama volume or copy GGUF files to removable media, import on the airgapped system. Llama 3.1 uses the Llama Community License (free for organizations under 700M MAU). Phi-4 models are MIT licensed (no restrictions). Foundation-Sec models use the Llama 3.1 Community License.

---

## 2. The LLM gateway layer: how BYOAPI actually works

Both prior reports mention "standardize on the OpenAI chat/completions API format" as the abstraction layer. That's correct but incomplete. You need an actual routing component that translates a single internal API call into the correct backend — whether that's local Ollama, a customer-hosted vLLM cluster, or a cloud API with authentication. This component exists: it's an **LLM gateway**.

### Why not LiteLLM

LiteLLM was the obvious choice until March 24, 2026, when it became the subject of one of the most significant supply chain attacks in the AI ecosystem. The TeamPCP threat group compromised the Trivy security scanner used in LiteLLM's CI/CD pipeline, stole the project's PyPI publishing tokens, and uploaded backdoored versions (1.82.7 and 1.82.8) that contained a three-stage credential stealer targeting SSH keys, cloud credentials, and Kubernetes secrets. The package sees 3+ million daily downloads. The backdoor was live on PyPI for approximately five hours before being quarantined. Given that LiteLLM typically sits between applications and multiple AI service providers with access to API keys and secrets, compromising it gave attackers a uniquely high-value position.

For a project like Malcolm that deploys into critical infrastructure and airgapped OT networks, depending on a package with this supply chain profile is unacceptable.

### Recommended: Mozilla any-llm as the gateway

**Mozilla any-llm** (Apache 2.0) is a Python library and optional FastAPI-based gateway server from Mozilla.ai that provides a unified interface across LLM providers. It takes a fundamentally different architectural approach than LiteLLM: it wraps the official provider SDKs rather than reimplementing them, reducing the surface area for compatibility bugs and supply chain risk.

Why any-llm fits Malcolm:

- **Ollama support built-in.** `pip install 'any-llm-sdk[ollama]'` gives you the Ollama provider. The gateway routes to `ollama/llama3.1:8b` or `ollama/phi4:14b` on the local Docker network.
- **any-llm-gateway for production.** The optional FastAPI gateway adds budget management (with automatic daily/weekly/monthly resets), virtual API key management, usage analytics, and multi-tenant support. It runs as a Docker container exposing an OpenAI-compatible API.
- **Mozilla provenance.** Mozilla.ai is a well-resourced American nonprofit with a strong security culture. The project uses official provider SDKs rather than reimplementing API interfaces, reducing the attack surface.
- **Clean LiteLLM migration path.** any-llm documents a migration path from LiteLLM — API keys and environment variables carry over unchanged.
- **No external network required in airgapped mode.** When configured with only the Ollama provider, zero outbound calls are made.

### Architecture with any-llm-gateway

```
┌─────────────────────────────────────────────────┐
│                  Malcolm Stack                   │
│                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐ │
│  │ Ollama   │   │ any-llm  │   │ Orchestrator │ │
│  │ (models) │◄──│ (gateway) │◄──│ (LangGraph)  │ │
│  └──────────┘   └────┬─────┘   └──────┬───────┘ │
│                      │                │          │
│              ┌───────┘         ┌──────┴───────┐  │
│              ▼                 │  MCP Servers  │  │
│     ┌────────────────┐        │ ┌───────────┐ │  │
│     │ Cloud APIs     │        │ │OpenSearch │ │  │
│     │ (if enabled)   │        │ │Arkime     │ │  │
│     │ Claude/OpenAI  │        │ │NetBox     │ │  │
│     │ vLLM cluster   │        │ │Malcolm API│ │  │
│     └────────────────┘        │ └───────────┘ │  │
│                               └───────────────┘  │
└─────────────────────────────────────────────────┘
```

### Configuration modes

Operators configure the LLM backend via environment variables and provider configuration, following Malcolm's existing patterns:

**Airgapped (default):**
```python
# Orchestrator uses any-llm SDK directly against local Ollama
from any_llm import completion

response = completion(
    model="llama3.1:8b",
    provider="ollama",
    messages=[{"role": "user", "content": "..."}]
)
```

**BYOAPI (cloud) — swap one parameter:**
```python
response = completion(
    model="claude-sonnet-4-20250514",
    provider="anthropic",
    messages=[{"role": "user", "content": "..."}]
)
```

**Gateway mode (multi-tenant, budget tracking):**
```bash
docker run \
  -e GATEWAY_MASTER_KEY="your-secure-master-key" \
  -e OLLAMA_HOST="http://ollama:11434" \
  -p 8000:8000 \
  ghcr.io/mozilla-ai/any-llm/gateway:latest
```

The gateway exposes an OpenAI-compatible API. Virtual keys with budget limits can be issued per user/team for BYOAPI deployments where cloud API costs need tracking.

### Fallback: direct Ollama API

For the simplest possible deployment, the orchestrator can skip the gateway entirely and call Ollama's OpenAI-compatible API directly at `http://ollama:11434/v1/`. This is the zero-dependency option for airgapped deployments that don't need multi-tenant budget tracking. The any-llm-gateway becomes relevant when operators want BYOAPI with usage tracking, or when multiple backend providers need unified routing.

---

## 3. The orchestrator: LangGraph + langchain-mcp-adapters

Both reports suggest LangGraph as the agent orchestration layer. This is the right call, and the integration path has become significantly clearer since those reports were written.

### langchain-mcp-adapters is the glue

The `langchain-mcp-adapters` package (from LangChain) provides `MultiServerMCPClient`, which connects a LangGraph agent to multiple MCP servers simultaneously via both `stdio` and `streamable_http` transports. The usage is minimal:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

client = MultiServerMCPClient({
    "opensearch": {"url": "http://opensearch-mcp:8080/mcp", "transport": "http"},
    "arkime":     {"url": "http://arkime-mcp:8081/mcp",     "transport": "http"},
    "netbox":     {"url": "http://netbox-mcp:8082/mcp",      "transport": "http"},
    "malcolm":    {"url": "http://malcolm-mcp:8083/mcp",     "transport": "http"},
})

tools = await client.get_tools()
agent = create_react_agent("openai:malcolm-orchestrator", tools)
```

This means the orchestrator container is a relatively thin Python service: it loads MCP tools at startup, binds them to a LangGraph ReAct agent, and exposes a chat API for the frontend. The LLM backend uses the any-llm SDK to call Ollama locally (default) or any cloud provider via BYOAPI configuration.

### What the orchestrator adds beyond raw MCP

The orchestrator container should implement:

- **Investigation state management.** The OAI report's "Investigation IR" concept is sound — each investigation is a structured object (JSON) with a query trail, evidence references, and pivot history. LangGraph's state graph is the natural implementation vehicle.
- **Tool budgets and cost caps.** Maximum OpenSearch queries per investigation, maximum Arkime hunt cost (estimated by document count), maximum tokens per session. Configurable via environment variables.
- **Human-in-the-loop gates.** For expensive operations (Arkime hunt across all indices, Suricata rule deployment), the agent pauses and requests analyst confirmation before proceeding.
- **Audit logging.** Every LLM prompt, tool call, and response is logged to a dedicated OpenSearch index (`malcolm_ai_audit-*`), providing full reproducibility and compliance evidence.
- **Prompt templates.** Stored in `./ai/prompts/` following Malcolm's drop-in pattern. Security-domain system prompts that teach the orchestrator about Malcolm's data model, field names, and investigation patterns. Analysts can customize without code changes.

### Why not OpenSearch's native agentic features?

OpenSearch 3.x ships agents, tools, and even a native MCP server. The OAI report wisely flags this as experimental surface area. The recommendation is: use OpenSearch's MCP server for OpenSearch-specific tools (PPLTool, SearchIndexTool, anomaly detection), but keep the orchestration logic in the dedicated LangGraph container. This avoids coupling investigation logic to OpenSearch's ML Commons release cycle, and it means the orchestrator works identically whether the backend is OpenSearch, Elasticsearch, or a future alternative.

---

## 4. Refined high-ROI feature specifications

### Feature 1: Natural-language query translator (highest immediate ROI)

**What it is:** An analyst types a question in natural language. The system generates an executable Arkime expression or OpenSearch query, shows it for review, runs it on approval, and returns structured results with links to Arkime session views.

**Why it's highest ROI:** Both reports identify query syntax fragmentation as Malcolm's primary barrier to entry. Malcolm supports Lucene, DQL, PPL, SQL, and Arkime expression syntax across different interfaces. New analysts spend days learning which syntax works where. This feature eliminates that barrier entirely while keeping the analyst in control — they see and approve every query.

**How it works:**

1. Analyst asks: "Show me unusual outbound TLS connections from the OT subnet in the last 6 hours"
2. Orchestrator (Llama 3.1 8B or Phi-4 14B) calls `malcolm_list_fields()` to understand available fields, then generates an Arkime expression: `ip.src == 10.0.100.0/24 && protocols == tls && ip.dst != 10.0.0.0/8 && databytes > 100000`
3. Orchestrator calls Arkime MCP's `build_query(expression, time_bounds)` — this uses Arkime's `/api/buildquery` endpoint to validate the expression and compile it to OpenSearch DSL. If the expression is invalid, the error message goes back to the LLM for correction.
4. Orchestrator calls `malcolm_aggregate()` to get a count estimate before execution.
5. Result is presented: the Arkime expression, the compiled DSL, the estimated document count, and an "Execute" prompt.
6. On approval, results come back as structured data: session count, top source/destination pairs, protocol breakdown, with clickable links to Arkime session views.

**Build scope:** Arkime MCP server (the `build_query` and `list_sessions` tools), Malcolm API MCP server (the `list_fields` and `aggregate` tools), and prompt templates for query generation. The orchestrator and any-llm gateway are prerequisites.

**What it explicitly does NOT do:** Summarize traffic in prose. The output is queries, counts, and links — not LLM-generated narratives about what the traffic means. The analyst interprets. The AI translates.

### Feature 2: Arkime MCP server (highest-leverage build target)

Both reports identify this as the most important missing component. The specification:

| Tool | Arkime endpoint | Purpose | Cost | Gate |
|------|----------------|---------|------|------|
| `build_query` | `/api/buildquery` | Validate expression → DSL | Low | None |
| `list_sessions` | `/api/sessions` | Return session metadata | Medium | Count preview |
| `get_session_detail` | `/api/session/:id/detail` | Single session deep dive | Low | None |
| `get_packets` | `/api/sessions/:id/packets` | Decoded packet content | Low | None |
| `export_pcap` | `/api/sessions/pcap` | Download matching PCAP | High | Requires confirmation |
| `run_hunt` | `/api/hunts` | Full-text packet search | Very high | Requires confirmation + cost estimate |

Implementation: a Python MCP server using `mcp` SDK with Streamable HTTP transport, running as a sidecar container on Malcolm's Docker network. Authenticates to Arkime via the same mechanism Malcolm's Nginx proxy uses (forwarded auth headers).

The guardrails matter: `export_pcap` and `run_hunt` must estimate cost (document count × average size) and require explicit analyst confirmation before execution. The MCP tool response should include a structured confirmation prompt, not just execute blindly.

### Feature 3: Malcolm API MCP server (small build, outsized impact)

Wraps Malcolm's `/mapi/` endpoints:

| Tool | Endpoint | Purpose |
|------|----------|---------|
| `malcolm_list_fields` | `/mapi/fields` | Schema-aware query construction |
| `malcolm_aggregate` | `/mapi/agg/{field}` | Quick summaries and counts |
| `malcolm_ingest_health` | `/mapi/ingest-stats` | Detect ingestion issues |
| `malcolm_document` | `/mapi/document` | Retrieve specific documents |
| `malcolm_version` | `/mapi/version` | System health check |

This is a small build (5 tools, straightforward REST wrapping) but provides the orchestrator with critical self-awareness: it can detect when missing data is an ingestion problem rather than a detection gap, and it can discover available fields dynamically rather than relying on hardcoded knowledge.

### Feature 4: Investigation workspace (medium build, high UX impact)

**What it is:** A persistent, structured record of an investigation that captures the query trail, evidence references, entity relationships, and analyst annotations. Not an LLM-generated report — a machine-readable, replayable case artifact.

**The OAI report's "Investigation IR" concept, made concrete:**

```json
{
  "investigation_id": "inv-2026-0342",
  "created": "2026-03-30T14:22:00Z",
  "analyst": "jsmith",
  "hypothesis": "Suspicious TLS beaconing from HMI-04",
  "steps": [
    {
      "step": 1,
      "action": "opensearch_query",
      "query": "src.ip:10.0.100.44 AND event.dataset:zeek.conn",
      "result_count": 847,
      "time_range": "2026-03-29T00:00:00Z/2026-03-30T14:00:00Z",
      "evidence_ids": ["session_abc123", "session_def456"]
    },
    {
      "step": 2,
      "action": "netbox_lookup",
      "query": "ip_address:10.0.100.44",
      "result": {"device": "HMI-04", "role": "hmi", "site": "Plant-A", "criticality": "high"},
      "analyst_note": "Confirmed this is a production HMI"
    }
  ],
  "entities": [
    {"type": "ip", "value": "10.0.100.44", "context": "HMI-04, Plant-A"},
    {"type": "ip", "value": "185.220.101.42", "context": "Tor exit node (confirmed via threat intel)"},
    {"type": "ja4_hash", "value": "t13d1516h2_8daaf6152771_...", "context": "Unusual TLS fingerprint"}
  ],
  "verdict": null,
  "exported": false
}
```

This structure is stored in OpenSearch (`malcolm_investigations-*`), versioned per step, and exportable as Markdown/JSON. The AI generates investigation steps; the analyst approves, annotates, and owns the verdict. The investigation can be replayed — every query re-executed to verify the results haven't changed.

**Build scope:** Medium. Requires a schema definition, an API endpoint in the orchestrator for CRUD operations, and a minimal UI element (could be an OpenSearch Dashboards plugin or a simple panel in Malcolm's landing page). The export-to-Markdown capability is trivial once the schema exists.

### Feature 5: AI-assisted onboarding and field coaching (low build, high adoption impact)

**What it is:** A help panel (or chat interface) that answers "what is this field?" and "how do I query X?" using RAG over Malcolm's local documentation and field catalogs.

**Why it matters more than it sounds:** Malcolm has hundreds of indexed fields across Zeek, Suricata, and Arkime, with different names and query syntax depending on which interface you're using. New analysts frequently don't know what fields exist, let alone how to query them. This is the single biggest barrier to Malcolm adoption.

**Implementation:** RAG over a curated corpus built at deploy time from Malcolm's docs, Arkime field definitions (`/api/fields`), and OpenSearch index mappings. Stored as embeddings in OpenSearch's vector search (k-NN plugin, already available). The orchestrator retrieves relevant context and generates answers. Every answer includes source citations and example queries.

**Build scope:** Light. The corpus curation is a one-time script. The vector index is standard OpenSearch. The RAG pipeline is a standard LangChain pattern. The main work is curating high-quality prompt templates that produce useful, accurate field explanations.

### Feature 6: Detection engineering assistant (medium-high build, strong community value)

**What it is:** An AI workflow that helps analysts draft Suricata rules, Zeek scripts, and YARA rules, test them against PCAPs, review the diff against existing rules, and deploy with explicit approval.

**Why it's high-value for open source:** This is the feature most likely to attract community contributors. Rule writing is a skill bottleneck — most Malcolm users can identify suspicious traffic but can't write a production-quality Suricata rule to detect it. An assistant that drafts candidates, runs them against test PCAPs, and reports false-positive risk dramatically lowers that barrier.

**The workflow:**

1. Analyst describes what they want to detect (natural language or by referencing a specific alert/session).
2. Foundation-Sec-8B-Reasoning generates a candidate rule with reasoning traces explaining each rule component.
3. Orchestrator runs the candidate against a test PCAP via Suricata MCP's `get_alerts_from_pcap_file()` (or Zeek MCP's `execzeek`).
4. Results come back: hit count, false positives on benign traffic, performance impact estimate.
5. Analyst reviews the diff against existing rules, modifies if needed, and explicitly approves deployment.
6. On approval, the rule is written to Malcolm's drop-in directory (`./suricata/rules/custom/` or `./zeek/custom/`).

**Build scope:** Medium-high. Requires the Suricata and Zeek MCP servers (community versions exist but need quality vetting), test PCAP management, and a careful approval workflow. The YARA path is simpler because YaraFlux MCP server already provides rule management, scanning, and string extraction.

---

## 5. MCP security: what both reports missed

The rapid growth of MCP adoption has surfaced real security concerns that Malcolm's AI layer must address from day one. Simon Willison documented the "Lethal Trifecta" of MCP risks: access to private data, exposure to malicious instructions, and the ability to exfiltrate information. The SAFE-MCP project (Linux Foundation / OpenID Foundation) provides a community-built security baseline. Cisco's DefenseClaw framework at RSAC 2026 includes an MCP Scanner tool for verifying MCP servers before deployment.

For Malcolm specifically:

- **No MCP server should have write access to production data without explicit confirmation gates.** Read-only by default. The Arkime MCP server can run queries but must not be able to modify sessions, tags, or delete PCAPs without human approval.
- **Every MCP tool call is logged.** The audit index (`malcolm_ai_audit-*`) records the tool name, input parameters, output summary, timestamp, and user identity.
- **MCP servers run with minimal permissions.** Each MCP server container gets read-only access to its specific service API and nothing else. The Arkime MCP server cannot reach OpenSearch directly; it goes through Arkime's API.
- **Prompt injection defense.** When the orchestrator processes data from OpenSearch query results (which may contain attacker-controlled strings in network traffic metadata), those results must be treated as untrusted data, not instructions. The prompt templates must clearly delineate "system instructions" from "retrieved data." This is a known-hard problem with no perfect solution, but careful prompt engineering and output validation reduce the attack surface significantly.
- **MCP server integrity verification.** Before deployment, MCP servers should be checksum-verified. In airgapped environments, this is part of the standard software transfer procedure. Community-contributed MCP servers should be reviewed before inclusion.

---

## 6. Community extensibility: the open-source story

Malcolm's strength is its open-source, composable architecture. The AI layer must extend this.

### What community contributors can build

- **Custom MCP servers.** The MCP SDK (Python or TypeScript) makes it straightforward to wrap any API as a set of tools. A contributor who wants Malcolm to integrate with their MISP instance, their MITRE ATT&CK navigator, or their ticketing system can build an MCP server and drop it into the registry.
- **Custom prompt templates.** Stored as plain text files in `./ai/prompts/`, these are the lowest-barrier contribution. An ICS specialist can write investigation prompt templates for Modbus anomaly analysis; a cloud security analyst can write templates for DNS tunneling detection.
- **Custom investigation playbooks.** The Investigation IR schema (Feature 4) can be templated — a contributor can publish a "ransomware investigation playbook" that pre-defines the investigation steps, and the orchestrator executes them with the analyst's approval at each step.
- **Fine-tuned models.** Community members with GPU resources can fine-tune Foundation-Sec-8B or Llama 3.1 8B on their organization's alert data, investigation notes, or detection rules, then share the resulting GGUF files.

### What the core team must build

- **The MCP server registry and discovery mechanism.** A YAML config that the orchestrator reads at startup to discover available tools. Adding a new MCP server = adding a stanza to this file and starting a container.
- **The any-llm gateway configuration interface.** Either a simple config file or environment variables that operators use to switch between airgapped, BYOAPI, and hybrid modes.
- **The orchestrator container with audit logging.** The core agent loop, state management, and human-in-the-loop gates.
- **The Arkime and Malcolm API MCP servers.** These don't exist anywhere and are specific to Malcolm.

### What should NOT be in the core

- **Hardcoded model preferences.** The core should be model-agnostic. Llama 3.1 8B, Phi-4 14B, and Foundation-Sec-8B-Reasoning are the US-origin defaults, but operators should be able to swap in any Ollama-compatible model — including non-US models like Qwen or Mistral if they choose.
- **Cloud-specific integrations.** BYOAPI support comes through any-llm provider configuration, not through direct Claude/OpenAI SDK calls in the orchestrator code.
- **Automated response actions.** Malcolm is an analysis tool, not a SOAR platform. The AI layer should never autonomously block traffic, quarantine hosts, or modify firewall rules. It assists investigation; humans decide response.

---

## 7. Revised phased roadmap

### Phase 0: Infrastructure (foundation, 2–3 weeks)

- Add Ollama container to Malcolm's Docker Compose with resource limits
- Add any-llm-gateway container with default airgapped configuration (Ollama provider only)
- Pull and package Foundation-Sec-8B-Reasoning Q4_K_M + Llama 3.1 8B Q4_K_M (+ Phi-4 14B Q4_K_M for 64GB+ systems)
- Document airgapped model transfer procedure
- Document BYOAPI configuration for Claude, OpenAI, and vLLM backends via any-llm providers

**Deliverable:** `malcolm-config.py` gains AI-related options; `docker-compose.yml` gains two new containers; operators can chat with the LLM via a simple API endpoint. No MCP, no orchestration yet — just the plumbing.

### Phase 1: Query translator + Arkime MCP (highest ROI, 4–6 weeks)

- Build Arkime MCP server (6 tools)
- Build Malcolm API MCP server (5 tools)
- Connect existing OpenSearch MCP server (standalone Python version)
- Connect existing NetBox MCP server (official)
- Build orchestrator container with LangGraph + langchain-mcp-adapters + any-llm SDK
- Implement natural-language query translation workflow
- Implement audit logging to OpenSearch

**Deliverable:** Analysts can ask questions in natural language and get executable, reviewable Arkime expressions. Cross-service pivoting works (Suricata alert → Zeek connection logs → Arkime session → NetBox asset lookup). Every AI interaction is logged.

### Phase 2: Investigation workspace + onboarding (UX, 4–6 weeks)

- Define Investigation IR schema
- Implement investigation CRUD API in orchestrator
- Build RAG pipeline over Malcolm docs + field catalogs
- Add minimal UI (OpenSearch Dashboards plugin or embedded panel)
- Implement investigation export (Markdown, JSON)

**Deliverable:** Investigations are persistent, replayable, and exportable. New analysts can ask "what is this field?" and get accurate, cited answers.

### Phase 3: Detection engineering + community extensibility (6–8 weeks)

- Vet and integrate Suricata and Zeek community MCP servers
- Build rule drafting + PCAP testing workflow
- Build MCP server registry with drop-in configuration
- Build prompt template directory with initial templates for common investigation types
- Document MCP server development guide for contributors
- Publish example MCP servers for MISP and MITRE ATT&CK

**Deliverable:** Analysts can draft and test detection rules with AI assistance. The community can extend Malcolm's AI capabilities by contributing MCP servers, prompt templates, and investigation playbooks.

---

## 8. Hardware reality check

Both prior reports claim 32GB+ RAM is sufficient. This needs nuance.

**Minimum viable AI deployment (32GB system RAM, CPU-only):**
- Malcolm core services: ~16GB (OpenSearch heap + Zeek + Suricata + Arkime + supporting containers)
- One quantized 8B model (Q4_K_M): ~5GB resident
- any-llm-gateway + orchestrator + MCP servers: ~2GB
- Available: ~9GB headroom
- **Verdict:** Feasible but tight. Only one model loaded at a time. Inference is slow (3–8 tok/s). Suitable for light use or evaluation.

**Recommended deployment (64GB system RAM, CPU-only):**
- Malcolm core services: ~20GB (larger OpenSearch heap for better query performance)
- Two quantized models loaded simultaneously (8B + 8B, or 14B + 8B): ~10–13GB
- any-llm-gateway + orchestrator + MCP servers: ~2GB
- Available: ~32GB headroom for OS cache and burst
- **Verdict:** Comfortable. Both models resident. Inference still CPU-bound but adequate for interactive use.

**Optimal deployment (64GB RAM + consumer GPU with 8GB+ VRAM):**
- Orchestrator model (Llama 3.1 8B or Phi-4 14B Q4_K_M) offloaded to GPU: 30–40 tok/s
- Reasoning model (Foundation-Sec-8B-Reasoning Q4_K_M) on CPU: 3–5 tok/s, but used less frequently
- **Verdict:** Interactive experience is responsive. Even a used RTX 3060 12GB or RTX 4060 transforms the experience.

The core principle: **the AI layer must degrade gracefully, not fail.** If hardware can only support one model, use Llama 3.1 8B for orchestration and skip Foundation-Sec-8B-Reasoning. If RAM is tight, limit concurrent model loads. If there's no AI hardware budget, the AI features simply don't activate and Malcolm works exactly as it does today.

---

## 9. Implementation architecture: three interaction paths

The AI integration serves three distinct consumer types, each with its own interface but sharing the same underlying engine.

### Path 1: Analyst widget

The primary interface for Malcolm analysts. A floating chat panel is injected via nginx `sub_filter` into every Malcolm page (Arkime, OpenSearch Dashboards, NetBox). The analyst types natural language queries or slash commands.

```
Analyst types "show DNS traffic from 10.0.0.1"
  → Widget sends POST /mapi/ai/v1/ask
  → Flask route calls ask() Python function
  → LLM selects arkime:open with filters
  → Route builds Arkime URL from ECS→Arkime field mapping
  → Widget auto-navigates browser to Arkime sessions view
```

The widget detects the current surface (Arkime, Dashboards, NetBox) and passes it as context so the LLM can make surface-aware routing decisions. Slash commands like `/search` bias the tool selection but the LLM makes all final decisions.

For workflows, the analyst can type natural language that matches a workflow trigger (e.g., "investigate weird anomalies") and the system runs the multi-step agent loop, returning results with navigation URLs.

### Path 2: MCP agent interface

For AI agents (Claude Desktop, Cursor, custom agents) that connect to Malcolm as a tool provider. The Malcolm MCP server exposes 12 high-value tools:

| Tool | Purpose |
|------|---------|
| `ask` | Natural language → tool selection |
| `workflow` | Multi-step investigation via agent loop |
| `explain_field` | Field documentation with cross-refs |
| `search_fields` | RAG search over field docs |
| `generate_rule` | LLM-powered Suricata rule generation |
| `generate_zeek_script` | LLM-powered Zeek script generation |
| `create_investigation` | Start an investigation |
| `add_investigation_step` | Record a step |
| `add_investigation_entity` | Track an entity (IP, hash, etc.) |
| `set_investigation_verdict` | Close an investigation |
| `list_investigations` | Find investigations |
| `export_investigation` | Export as markdown/JSON |

For agents that need raw data access, the service MCPs (Arkime, OpenSearch, Malcolm API, NetBox) are available as opt-in connections with 3-5 tools each.

The MCP interface does NOT require the Flask API to be running. MCP servers start independently via `run_servers.py`.

### Path 3: REST API for external tooling

For programmatic integration by scripts, CI/CD pipelines, external platforms, or any HTTP client. The REST API provides the same capabilities as the MCP interface:

```
POST /mapi/ai/v1/ask       — Natural language query
POST /mapi/ai/v1/workflow   — Run a multi-step workflow
GET  /mapi/ai/v1/status     — Health check / feature discovery
GET  /mapi/ai/v1/commands   — Available slash commands
```

The API does NOT require MCP servers to be running. It calls the same Python functions directly. An external system can trigger a workflow via API, receive structured results with Arkime session URLs and PCAP download links, and incorporate those into its own process.

### Independence of paths

Each path calls the same underlying Python functions but through different transports:

```
Widget ──→ Flask routes ──→ ask(), workflow()
MCP    ──→ @mcp.tool()  ──→ ask(), workflow()
API    ──→ Flask routes ──→ ask(), workflow()
```

No path depends on another being active. The Flask API works without MCP servers. The MCP servers work without the Flask API. The widget works through the Flask API alone.

### Workflow YAML as a community artifact

Workflows are defined in YAML files (`config/workflows.yml`) with step-by-step goals, constrained tool choices per step, and per-step model overrides. Model resolution follows the chain: `step.model → workflow.model → config.default_model`.

Workflows are shareable artifacts. A DFIR team can write a "ransomware investigation" workflow YAML, share it, and anyone with Malcolm can drop it in and run it. The workflow self-documents its model requirements — if a step specifies `model: cisco-foundation-sec-8b-reasoning`, the system uses it if available and falls back to the default if not.

External agents can also BUILD workflows: connect via MCP, use the raw tools to discover patterns, then write a YAML workflow that codifies those patterns for repeatable use.

### Key architectural decisions

- **LangChain/LangGraph rejected.** Tested live — phi4 doesn't support Ollama's native tool-calling API, and llama3.1:8b can't chain multi-step reliably. The hand-rolled agent loop uses `any-llm` + `response_format=Pydantic` for structured output, which works with all models.
- **Service MCPs are API bridges, not AI.** No LLM involvement. They translate MCP tool calls to REST API calls against Arkime, OpenSearch, etc.
- **Malcolm MCP is the AI orchestrator.** It's the only MCP server that calls the LLM. External agents connect here for AI-assisted operations.
- **The agent loop hand-holds small models.** Workflow steps constrain available tools and provide explicit instructions. The LLM decides one step at a time, not the entire plan. If a 8B model works correctly, a larger model will work even better.

# Malcolm AI Integration — Branch Overview

**Branch:** `malcolm-ai-integration`  
**63 commits, 101 files changed (95 new, 6 modified), ~8,500 lines** (7,600 Python/YAML + 906-line JS widget)

## What This Is

An AI-powered analyst assistant for Malcolm that works in two modes:

1. **Analyst Widget** — A floating chat panel injected into every Malcolm UI page. Analysts type natural language or slash commands to navigate dashboards, search sessions, and investigate alerts. Runs on a local 3B-parameter LLM (llama3.2:3b) via Ollama for air-gapped deployments.

2. **MCP Interface** — External AI agents (Claude, Gemini, ChatGPT, custom SOC tools) connect via Model Context Protocol to query Malcolm programmatically. Malcolm is a "useful dumb tool" that exposes its data — it doesn't try to be the AI.

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │         External AI Agents           │
                    │   (Claude, Gemini, SOC tooling)      │
                    └────────────┬─────────────────────────┘
                                 │ MCP
                    ┌────────────▼─────────────────────────┐
                    │      Malcolm MCP Server              │
                    │  (ask, pivot, workflow, dashboards)  │
                    │         malcolm_server.py            │
                    └────────────┬─────────────────────────┘
                                 │
              ┌──────────────────┼─────────────────┐
              │                 │                  │
    ┌─────────▼──────┐ ┌────────▼───────┐ ┌────────▼───────┐
    │   SafetyGate   │ │  Agent Loop    │ │   Workflows    │
    │  (tool routing)│ │ (multi-step)   │ │  (YAML-driven) │
    └─────────┬──────┘ └────────┬───────┘ └────────┬───────┘
              │                 │                  │
              ▼                 ▼                  ▼
    ┌─────────────────────────────────────────────────────┐
    │              Tool Registry (agent/tools.py)         │
    │    Direct HTTP calls to Arkime, OpenSearch, NetBox  │
    └──────────────────────┬──────────────────────────────┘
                           │
         ┌─────────────────┼────────────────┐
         │                 │                │
    ┌────▼────┐      ┌─────▼─────┐     ┌────▼────┐
    │ Arkime  │      │ OpenSearch│     │ NetBox  │
    └─────────┘      └───────────┘     └─────────┘

    ┌──────────────────────────────────────────────┐
    │            Analyst Chat Widget               │
    │    (nginx-injected JS, non-Shadow-DOM)       │
    │         malcolm-ai-widget.js                 │
    │                    │                         │
    │     Flask routes (routes.py)                 │
    │         │                                    │
    │    Malcolm MCP Server (same as above)        │
    └──────────────────────────────────────────────┘
```

## Code Location

All AI code lives in `api/project/ai/`. The widget is `nginx/landingpage/js/malcolm-ai-widget.js`.

### Directory Layout

```
api/project/ai/
├── mcp/                          # MCP servers (what we ship)
│   ├── malcolm_server.py         #   AI orchestrator — ask, pivot, workflow, dashboards
│   ├── malcolm_api_server.py     #   Wraps Malcolm's own /mapi/ endpoints
│   └── run_servers.py            #   Dev launcher
│
├── llm/                          # LLM interaction layer
│   ├── client.py                 #   Provider-agnostic calls via any-llm
│   ├── safety.py                 #   SafetyGate — tool routing with safety check
│   └── prompts.py                #   Context-aware system prompt building
│
├── agent/                        # Multi-step agent for investigations
│   ├── loop.py                   #   Agent execution loop (plan → tool → observe)
│   ├── models.py                 #   StepDecision, AgentState, AgentResult
│   ├── tools.py                  #   ToolRegistry — direct HTTP to services
│   └── workflows.py              #   WorkflowRegistry — YAML workflow loader
│
├── config/                       # All YAML-driven configuration
│   ├── ai.yml                    #   Engine config (default model, safety mode)
│   ├── loader.py                 #   YAML loader with ${ENV_VAR:-default} interpolation
│   ├── settings.py               #   Typed AISettings from ai.yml
│   ├── tools/                    #   Tool manifest (single source of truth)
│   │   ├── manifest.yml          #     UI-level → service-level tool mapping
│   │   ├── catalog.py            #     ToolManifest loader
│   │   └── translators.py        #     Param translation functions
│   ├── models/*.yml              #   Model cards (20+ Ollama/Anthropic models)
│   ├── models/catalog.py         #   ModelCatalog — glob-based auto-discovery
│   ├── commands/*.yml            #   Slash command definitions (5 commands)
│   ├── commands/catalog.py       #   CommandCatalog
│   ├── dashboards/*.yml          #   Dashboard metadata (61 dashboards, profiles, pivots)
│   ├── dashboards/catalog.py     #   DashboardCatalog — search, URL generation
│   ├── prompts/<adapter>/*.yml   #   Per-tool few-shot examples and field guides
│   ├── prompts/catalog.py        #   PromptCatalog
│   └── workflows/*.yml           #   Multi-step investigation definitions (5 workflows)
│
├── connections.py                # Centralized service connection config
├── context.py                    # Filter merging + time inheritance
├── enrichment.py                 # Deterministic artifact extraction (regex)
├── field_mappings.py             # ECS ↔ Arkime field translation + URL builders
├── audit.py                      # Audit logger (writes to OpenSearch)
├── rag.py                        # RAG pipeline for field documentation search
├── routes.py                     # Flask routes bridging widget → MCP
│
├── tests/
│   ├── benchmark_models.py       #   Model benchmark framework (14 queries)
│   ├── test_integration.py       #   Integration tests
│   └── test_widget_playwright.py #   Widget test case documentation
│
nginx/landingpage/js/
└── malcolm-ai-widget.js          # Analyst chat widget (906 lines)
```

## Key Components

### 1. Tool Manifest — Single Source of Truth

`config/tools/manifest.yml` defines the 7 UI-level tools the LLM selects from:

| UI Tool | Maps To | Purpose |
|---------|---------|---------|
| `arkime:open` | `arkime:list_sessions` | Open Arkime sessions with ECS filters |
| `opensearch_dashboards:open` | `opensearch:search` | Navigate to a dashboard by topic |
| `opensearch_dashboards:filter` | `opensearch:search` | Apply filters/time to current dashboard |
| `netbox:search` | `netbox:lookup_device` | Look up device/IP in NetBox |
| `arkime:export` | `arkime:export_pcap` | Build PCAP export URL |
| `strelka:open` | `opensearch:search` | Open file scanning results |
| `unknown` | — | Off-topic/adversarial rejection |

Each entry has a `translator` function that converts ECS-style params to service-native params. Adding a tool = add YAML + translator function. SafetyGate, routes, and prompts all derive from this file.

### 2. SafetyGate — LLM Tool Routing

`llm/safety.py` — Currently two modes, with a three-step architecture planned:

**Current modes:**
- **Single mode** (default): One LLM call returns safety check + tool + params
- **Two-step mode**: Fast model picks the tool (from 7 choices), default model fills params with per-tool schema

**Planned three-step architecture:**

| Step | Decision | Complexity | Model |
|------|----------|-----------|-------|
| 1. Safety | Is this query safe? | Binary yes/no | Tiny model or deterministic pre-hook |
| 2. Routing | Which of 7 tools? | Pick from small list | Fast 3B model (llama3.2:3b) |
| 3. Params | What does the tool need? | Extract filters, time, IPs from natural language | Potentially bigger model per-tool |

Step 3 is where per-tool model overrides would make sense — after routing decides the tool, a more capable model could fill complex params. A simple `netbox:search` just needs a query string (3B is fine), but `arkime:open` with compound filter logic might benefit from a larger model. This split is deferred until single-mode accuracy becomes insufficient.

**Model selection (current):**
1. `ai.yml` `default_model` → used for all LLM calls (single mode) or param filling (two-step)
2. `ai.yml` `router_model` → used for step 1 routing only (two-step mode)
3. Model card YAML → connection details for whichever model was selected
4. any-llm → Ollama/Anthropic

No per-tool or per-command model overrides exist today. One model does the work. This is intentional — the model must be chosen before the LLM picks a tool, so tool-specific model selection is inverted in single-mode. Per-tool models only become viable when step 2 (routing) and step 3 (params) are separate LLM calls.

**Prompt context** — the LLM receives a context-aware system prompt built from:
- Current surface (arkime, opensearch_dashboards, netbox)
- Dashboard profile (data source, primary fields, pivot affordances)
- Pre-extracted artifacts (IPs, ports, domains, hashes, protocols, time ranges)
- Slash command verb hints
- Per-adapter few-shot examples

### 3. Analyst Widget

`malcolm-ai-widget.js` — Injected into every Malcolm page via nginx `sub_filter`:

- **Floating draggable button** with spinning Malcolm wheel on hover
- **Resizable chat panel** with drag-to-resize handle
- **Chat history** persisted in localStorage (survives tab close)
- **Slash command autocomplete** — type `/` to see available commands
- **Abort/cancel** — Stop button during LLM inference
- **Export/clear** chat history
- **Collapse** mode (minimize to just header + input)
- **Auto-navigation** — LLM response builds a URL, widget navigates to it
- **SPA persistence** — MutationObserver re-attaches widget after React/Angular DOM replacement
- **Context scraping** — reads dashboard UUID, time range, filters, Arkime expression from URL

### 4. Slash Commands

5 commands defined in `config/commands/*.yml`:

| Command   | Purpose |
|-----------|---------|
| `/open`   | Open a dashboard or Arkime view |
| `/search` | Search across any surface |
| `/filter` | Apply filters or change time range on current view |
| `/export` | Export PCAP or search results |
| `/pivot`  | Translate filters to a different surface |

Commands are **verb hints for the LLM**, not fast-paths or authoritative directives. The LLM makes all routing decisions. Commands carry no execution logic, no model overrides, and no tool bindings — they only provide per-surface descriptions that help the LLM understand what actions are possible on the current surface. A user typing `/filter 10 years` is giving the LLM a nudge, not invoking a deterministic code path.

### 5. Workflows

5 investigation workflows in `config/workflows/*.yml`:

| Workflow | Steps | Purpose |
|----------|-------|---------|
| `investigate_alert` | 3 | Suricata alert → community_id → Arkime sessions |
| `investigate_connection` | 2 | IP → all sessions + protocol breakdown |
| `investigate_weird` | 4 | Zeek weird → conn logs → Arkime → PCAP |
| `alert_triage` | 3 | High-severity alerts → session details → NetBox |
| `file_investigation` | 3 | File scan → session lookup → PCAP |

Each workflow defines steps with constrained tool sets, prompt templates with `{param}` substitution, and `{prev.field}` data threading between steps.

### 6. Deterministic Enrichment

Python handles all facts the LLM shouldn't guess:

- **Artifact extraction** (`enrichment.py`): IPs, ports, domains, Suricata SIDs, community IDs, file hashes, protocols (50+), time ranges — all via regex
- **Field mappings** (`field_mappings.py`): 70 ECS ↔ Arkime field translations, Arkime expression builder (handles list values as OR groups), Lucene query builder, URL builders
- **Context merging** (`context.py`): `merge_filters()` accumulates same-key values into lists; `inherit_time_range()` uses LLM time if provided, else inherits from UI

### 7. YAML Plug-and-Play

Everything is auto-discovered via glob. Drop a file, it works:

| What | Directory | Discovery |
|------|-----------|-----------|
| Model cards | `config/models/*.yml` | ModelCatalog globs `*.yml` |
| Dashboards | `config/dashboards/*.yml` | DashboardCatalog globs `*.yml` |
| Commands | `config/commands/*.yml` | CommandCatalog globs `*.yml` |
| Workflows | `config/workflows/*.yml` | WorkflowRegistry globs `*.yml` |
| Prompt hints | `config/prompts/<adapter>/<action>.yml` | PromptCatalog walks directories |
| Tool manifest | `config/tools/manifest.yml` | ToolManifest loads single file |

### 8. External Service MCPs

Malcolm does **not** ship MCP servers for services it doesn't own. Official external MCPs exist:

| Service | MCP Server | Maintained By |
|---------|-----------|---------------|
| OpenSearch | [opensearch-mcp-server-py](https://github.com/opensearch-project/opensearch-mcp-server-py) | AWS/OpenSearch Project |
| NetBox | [netbox-mcp-server](https://github.com/netboxlabs/netbox-mcp-server) | NetBox Labs |
| Arkime | [arkime-mcp-server](https://glama.ai/mcp/servers/swannman/arkime-mcp-server) | Community |

### 9. Model Benchmarks

40 runs across 19 models using a 14-query benchmark suite (`python -m ai.tests.benchmark_models`).

**Default model: llama3.2:3b** (Meta, Llama Community License) — 11–12/14 routing accuracy, ~1.3s average. Chosen over qwen2.5:3b (13/14) for US-origin supply-chain trust; accuracy delta is within one query on the 14-query suite.

Full leaderboard in `tests/benchmark_results/results.jsonl`.

## Configuration

### Engine Config (`config/ai.yml`)

```yaml
default_model: ${AI_DEFAULT_MODEL:-llama-3_2-3b}
safety:
  mode: ${AI_SAFETY_MODE:-single}      # single, two_step, disabled
  router_model: ${AI_ROUTER_MODEL:-}   # fast model for two_step mode
audit:
  enabled: ${AI_AUDIT_ENABLED:-false}
  index: malcolm_ai_audit
```

### Centralized Connections (`connections.py`)

All service URLs, auth, and SSL config in one place. Used by audit, RAG, agent tools, MCP servers, and routes.

### Docker Dev Setup (`docker-compose.override.yml`)

- API container bind-mounts `api/project/` for hot-reload
- Nginx bind-mounts widget JS and wheel SVG
- `AI_ENABLED=true`, `OLLAMA_URL=http://172.19.0.1:11434`

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/mapi/ai/v1/status` | Is AI enabled? Widget checks before rendering |
| POST | `/mapi/ai/v1/ask` | Natural language query → tool selection + navigation |
| POST | `/mapi/ai/v1/workflow` | Run multi-step investigation workflow |
| GET | `/mapi/ai/v1/commands` | List available slash commands |
| POST | `/mapi/ai/v1/execute` | Execute a tool directly (disabled by default) |

## Design Principles

1. **Air-gapped first** — Everything works offline with local Ollama models
2. **Malcolm is a tool, not a command center** — MCP for external agents, widget for convenience
3. **Python handles facts, LLM handles language** — Deterministic enrichment before LLM, deterministic execution after
4. **YAML plug-and-play** — Drop a file, system discovers it. No code changes.
5. **Single source of truth** — Tool manifest owns tool definitions, connections.py owns service config
6. **Two-tier tool names** — Small vocabulary (7 tools) for 3B model accuracy, service-level tools (16+) for execution
7. **any-llm is the standard** — No custom LLM backends. Provider-agnostic via Mozilla's any-llm SDK.

## What's NOT Here

- **No LangChain/LangGraph** — Hand-rolled agent loop, simpler and controllable
- **No session memory** — Stateless API, widget scrapes context from URL
- **No custom service MCPs** — Use official external MCPs for Arkime/OpenSearch/NetBox
- **No Shadow DOM** (yet) — Widget injects CSS directly for Playwright testing. Shadow DOM planned for production CSS isolation.

## Known Issues / Next Steps

- Agent ToolRegistry duplicates HTTP client logic that external MCPs would provide
- Widget non-Shadow-DOM may conflict with host page CSS in some edge cases
- AI subsystem will eventually move to its own Docker container
- Strelka MCP server doesn't exist yet (strelka:open routes through OpenSearch)
- Workflow YAML format is beta — may evolve toward more structured `tool: / args:` steps
- Three-step safety/routing/params split is designed but deferred — single mode at 13/14 accuracy is sufficient for now
- Per-tool model overrides require the three-step split to be viable (model chosen before tool is known in single mode)
- `field_mappings.py` duplicates knowledge from Malcolm's `docs/queries-cheat-sheet.md` — could be generated from it or from Arkime's `/api/fields` endpoint at runtime in the future

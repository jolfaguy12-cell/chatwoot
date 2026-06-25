# Chatwoot — Architecture Graph Report (AST Extraction)

Generated: 2026-06-25 (AST-only; no LLM clustering)  
Graph: `graphify-out/graph.json` — 18,177 nodes · 26,484 links

---

## Codebase Composition

| Language | Nodes |
|---|---|
| Ruby (`.rb`) | 10,795 |
| JavaScript (`.js`) | 3,437 |
| Vue components (`.vue`) | 3,422 |
| JSON configs | 280 |
| Markdown docs | 144 |
| Shell scripts (`.sh`) | 51 |
| TypeScript (`.ts`) | 28 |

---

## Directory Structure (top-level)

| Directory | Nodes | Purpose |
|---|---|---|
| `app/` | 12,869 | Core Rails app (models, controllers, services, jobs, channels, views) |
| `enterprise/` | 2,190 | Enterprise edition overlays (mirrors `app/` structure) |
| `spec/` | 1,008 | RSpec tests |
| `lib/` | 976 | Libraries, custom exceptions, tasks, utilities |
| `db/` | 469 | Migrations, schema, seeds |
| `config/` | 92 | Initializers, routes, environments |
| `tests/` | 92 | Frontend Vitest/Jest tests |
| `wp-plugin/` | 33 | Behdashtik WordPress plugin |
| `docs/` | 24 | Documentation |

---

## High-Connectivity Hub Nodes

These nodes are most-referenced across the codebase (most edges pointing to them):

| References | Node | Significance |
|---|---|---|
| 178 | `.merge()` | ActiveRecord query building — widely used across models/services |
| 142 | `.create!()` | Model record creation — core persistence pattern |
| 129 | `mutation-types.js` | Vuex mutation constants — referenced from all Vuex store modules |
| 112 | `api_apiclient` | Frontend API client base — all API calls route through it |
| 111 | `ApiClient` | Frontend API client class |
| 88 | `current()` | Current context/scope — models use `Current.account`, `Current.user` |
| 82 | `vue` | Vue framework import — all Vue components |
| 74 | `.load()` | Module/asset loading pattern |
| 65 | `last()` | ActiveRecord query tail — used in seeding, specs |
| 64 | `axios` | HTTP client — widget and frontend API calls |

---

## Key Architectural Boundaries

- **OSS / Enterprise split**: All OSS code is in `app/`, `lib/`, `config/`. Enterprise overlays are in `enterprise/` and use `prepend_mod_with` / `include_mod_with` extension points.
- **Frontend**: Vue 3 (Composition API) + Vuex. API calls via `app/javascript/dashboard/api/` (axios-based `ApiClient`). Widget is separate: `app/javascript/widget/`.
- **Backend**: Rails API mode. Services in `app/services/`, jobs in `app/jobs/`, channels (ActionCable) in `app/channels/`.
- **Behdashtik overlays**: `app/controllers/api/v1/behdashtik/`, `app/services/behdashtik/`, `public/js/behdashtik-journey-tracker.js`, `wp-plugin/`.

---

## How to Use This Graph

```bash
# Ask a question about the codebase
graphify query "How does conversation assignment work?"

# Trace relationship between two concepts
graphify path "ConversationAssignment" "ContactInbox"

# Explain a node
graphify explain "Messages::MessageBuilder"

# Rebuild graph after code changes (no LLM cost)
graphify update . --no-cluster
# OR use the task runner:
scripts/ai-task --update-graph
```

**Do not read `graph.json` directly** — it is 14 MB of raw JSON. Use the CLI commands above.

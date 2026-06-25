# AI Development Workflow — Chatwoot / Behdashtik

This document explains how Graphify + the `scripts/ai-task` runner are configured, and how to use them to speed up Claude/Codex tasks.

---

## What Is Set Up

| Component | What it does |
|---|---|
| **Graphify** | Builds a persistent knowledge graph (`graphify-out/`) from AST extraction — no LLM cost. Lets agents `query`, `path`, and `explain` concepts without reading raw files. |
| **`scripts/ai-task`** | Thin shell wrapper that ensures the graph is built before a task runs. Only rebuilds when missing or when `--update-graph` is passed. |
| **AGENTS.md / CLAUDE.md rules** | Tell agents to use the graph first, avoid full-directory reads, run targeted tests, and emit compact (Caveman-style) final reports. |
| **`.claude/settings.json` hooks** | Intercept grep/find/Read calls — if `graph.json` exists, the agent is reminded to use `graphify query` first. |

---

## Graph Files

```
graphify-out/
├── graph.json         ← 14 MB raw graph (do NOT read directly)
├── manifest.json      ← file inventory
├── GRAPH_REPORT.md    ← human-readable architecture summary (read this)
└── cache/             ← AST extraction cache (speeds up incremental updates)
```

---

## How to Use scripts/ai-task

```bash
# Check graph status (no rebuild)
scripts/ai-task

# Force rebuild after large code changes (AST-only, no LLM, ~1-2 min)
scripts/ai-task --update-graph

# Rebuild then run a command
scripts/ai-task --update-graph -- bundle exec rspec spec/services/

# Just ensure graph exists, then run (skips rebuild if graph is current)
scripts/ai-task -- bundle exec rspec spec/models/conversation_spec.rb
```

The graph is NOT rebuilt on every run. It is only built when:
1. `graphify-out/graph.json` does not exist, OR
2. `--update-graph` is explicitly passed

---

## How Agents Should Use the Graph

### For broad architecture questions
```bash
# Read the summary first (small file)
cat graphify-out/GRAPH_REPORT.md

# Then query for specifics
graphify query "How does conversation assignment work?"
```

### For tracing relationships
```bash
graphify path "ConversationsController" "Messages::MessageBuilder"
graphify path "ContactInbox" "Conversation"
```

### For explaining a concept
```bash
graphify explain "Widget::TokenService"
graphify explain "Rack::Attack throttle"
```

### After modifying code
```bash
# Update graph to reflect changes (AST-only, no LLM)
graphify update . --no-cluster
# or via task runner:
scripts/ai-task --update-graph
```

---

## Caveman-Style Reports

Every completed AI task ends with a compact report:

```
## Done
- Files changed: app/services/foo.rb, app/models/bar.rb
- Tests run: bundle exec rspec spec/services/foo_spec.rb → 12 examples, 0 failures
- Risks: none
```

No prose. No "As you can see..." or "I have successfully...". One bullet per item.

---

## When to Rebuild the Graph

| Situation | Action |
|---|---|
| Graph missing (first run) | `scripts/ai-task` auto-builds |
| After adding new files/services | `scripts/ai-task --update-graph` |
| After a large refactor | `scripts/ai-task --update-graph` |
| Routine bug fix (few files changed) | No rebuild needed |
| CI/CD pre-task check | `scripts/ai-task` (no-op if current) |

---

## Costs and Safety

- `graphify update . --no-cluster` — **zero API cost**, zero LLM calls. Pure AST parsing.
- `graphify update .` (without `--no-cluster`) — runs clustering (still no LLM if no API key is set)
- `graphify extract .` — full pipeline including semantic LLM extraction (costs API tokens)
- **`scripts/ai-task` always uses `--no-cluster`** — safe to run in DEV, no API charges.
- **No production impact** — all files are under `graphify-out/` (git-ignored recommended), `scripts/`, and `docs/ai/`. No app runtime files touched.

---

## Graphify Skill (Claude Code)

The `/graphify` skill is installed at `.claude/skills/graphify/SKILL.md`. To use it:

```
/graphify                          # run full pipeline on current directory
/graphify query "How does X work?" # query existing graph
/graphify --update                 # incremental update
```

Claude Code's PreToolUse hooks (in `.claude/settings.json`) automatically remind the agent to run `graphify query` before grepping raw files when a graph exists.

---

## Rollback / Cleanup

To remove everything:
```bash
rm -rf graphify-out/
rm scripts/ai-task
rm docs/ai/AI_DEV_WORKFLOW.md
rm -rf .claude/skills/graphify/
# Remove the graphify section from AGENTS.md (last 30 lines)
# Remove hooks from .claude/settings.json
# Optionally: graphify uninstall --purge
```

Nothing in the Chatwoot app runtime is changed by this setup.

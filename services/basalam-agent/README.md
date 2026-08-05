# Behdashtik AI Support Agent

AI customer-support agent for the Behdashtik store. Answers Persian customer
messages in Chatwoot about products, orders/tracking and store policies using
live Behdashtik Hub data, with human handoff over Telegram and a full admin
UI inside the Chatwoot dashboard.

## Architecture

```
Customer (widget, dev.behdashtik.ir)
   │
Chatwoot (support.behdashtik.ir) ── AgentBot webhook (HMAC) ──► this service
   ▲                                                             │
   │ bot API (replies, status, assignment)                       │ LangGraph agent
   │ user API (history reads)                                    │  ├ intent routing (cheap model)
   │                                                             │  ├ deterministic order authorization
Telegram operators ◄── fanout/claim/replies ── PTB webhook ──────┤  ├ tool loop → Hub agent API
                                                                 │  └ validation → send / handoff
Hub (hub.behdashtik.ir/api/agent/v1) ◄── product/order/policy ───┘
```

- **FastAPI** single-worker service (`systemd: bdsk-ai-agent`, port 8010,
  public path `https://support.behdashtik.ir/ai-agent/` via nginx).
- **LangGraph + LangChain** agent; all models called through **OpenRouter**
  (any OpenAI-compatible provider can be added from the dashboard).
- **SQLite (WAL)** at `data/agent.db` owns all state: providers, models,
  role assignments, prompt versions, settings, conversation control mode,
  message-job queue, response telemetry, evaluations, test cases, content
  gaps, handoffs, Telegram operators, audit log. Config is hot-loaded per
  run — **no restarts needed for any dashboard change**.
- **Telegram bot** (python-telegram-bot v21, webhook mode) for operator
  linking, handoff fanout with atomic claim, reply routing, resolve /
  return-to-AI.
- **Langflow is deliberately not used**: it would add a whole extra server
  and auth surface only to duplicate the DB-backed prompt/role/routing
  configuration the Chatwoot dashboard already provides. **LangSmith** is
  optional (env + settings toggle, off by default; enable only with a
  scrubbing policy for customer PII).

## Conversation ownership model

`conversations.mode` in the service DB is the single source of truth:

| mode | Chatwoot status | behavior |
|---|---|---|
| `ai` | `pending` | agent replies; operators may watch/intervene |
| `human` | `open` | agent silent; messages relayed to the claiming operator |
| `disabled` | any | agent silent (per-conversation pause) |

Handoff = mode→human + status→open + Telegram fanout. Return-to-AI (Telegram
button, dashboard, or setting the conversation back to Pending) = mode→ai +
status→pending. Resolving closes open handoffs and re-arms the AI.

## Reliability

- Webhooks are HMAC-verified (timestamp-fresh), deduped
  (`processed_events`), and acked fast; work happens in a durable
  per-conversation queue (`message_jobs`) with debounce-merge, 3 attempts
  with backoff, and startup recovery. Final failure ⇒ Persian apology +
  human handoff — never silence.
- If this service is down entirely, Chatwoot's own agent-bot retry
  exhaustion flips `pending` conversations to `open` so humans see them.
- Every LLM call: primary model → 1 retry → fallback model → handoff.
- Order data is authorized **in application code** (phone/email matched
  against Hub before anything reaches the model); the model's order tool
  exposes only pre-authorized orders and takes no identity parameters.
- Retrieved text is wrapped in `<data>` blocks + injection-pattern
  neutralization; a validation pass checks groundedness/scope/Persian
  before sending (two failures ⇒ handoff).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in secrets (see comments in the file)
.venv/bin/python -m scripts.seed_defaults
# register the Chatwoot AgentBot (prints bot token + webhook secret for .env):
docker cp scripts/register_agent_bot.rb chatwoot-rails-1:/tmp/ && \
  docker exec chatwoot-rails-1 bundle exec rails runner /tmp/register_agent_bot.rb
sudo systemctl enable --now bdsk-ai-agent   # deploy/bdsk-ai-agent.service
```

nginx: add the location block from `deploy/nginx-ai-agent.conf` to the
`support.behdashtik.ir` vhost (already applied on this server).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q          # service unit tests
.venv/bin/python -m scripts.smoke_webhook     # signed webhook smoke test
```

See `docs/runbook.md` for operations, verification scenarios and rollback.

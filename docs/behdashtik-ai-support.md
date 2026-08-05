# Behdashtik AI Support Agent — Chatwoot integration

The AI agent itself is a separate service (`/root/behdashtik-ai-agent`,
systemd `bdsk-ai-agent`, docs there). This file lists what lives in the
Chatwoot repo.

## How it connects

- **AgentBot** "Behdashtik AI Assistant" (webhook type) attached to inbox 12
  (Behdashtik — behdashtik.ir) and inbox 1 (Behdashtik-Dev). New conversations
  start `pending` (bot-owned); the service replies via the bot API and keeps
  them pending; handoff opens them.
  Registration script: `behdashtik-ai-agent/scripts/register_agent_bot.rb`.
- **Which store answers** is decided by the inbox: the service maps inbox 12 →
  main hub (behdashtik.ir data) and everything else → dev hub. See the
  service runbook, "Two sites, two hubs".
- **Reads** use the `agent-claude@behdashtik.ir` operator account's access
  token (the bot allowlist has no read endpoints).
- Env (`.env`): `BEHDASHTIK_AI_SERVICE_URL`, `BEHDASHTIK_AI_ADMIN_TOKEN` —
  used by the Rails proxy below.

### Two agents, one panel

The Basalam shop has its own agent (`/root/behdashtik-basalam-agent`, systemd
`bdsk-basalam-agent`, AgentBot "Basalam AI" on inbox 13) with its own SQLite,
prompts, models and operator Telegram bot. Both are managed from the same
**AI Assistant** section; the `service` query parameter picks which one:

| `service` | URL env | Token env |
|---|---|---|
| `site` (default) | `BEHDASHTIK_AI_SERVICE_URL` | `BEHDASHTIK_AI_ADMIN_TOKEN` |
| `basalam` | `BEHDASHTIK_AI_BASALAM_SERVICE_URL` | `BEHDASHTIK_AI_BASALAM_ADMIN_TOKEN` |

An unknown value falls back to `site`. Operators link to each bot separately —
Profile settings shows one Telegram panel per service.

## Chatwoot-side code

| Path | Purpose |
|---|---|
| `app/controllers/api/v1/accounts/behdashtik_ai/proxy_controller.rb` | Authenticated pass-through `/api/v1/accounts/:id/behdashtik_ai/*` → AI service `/admin/v1/*`. Admin-only except the agent allowlist (telegram self-service, evaluation submit). Injects `X-Chatwoot-User-Id/Name`; overwrites evaluation rater identity server-side. |
| `app/services/behdashtik/visitor_journey_service.rb#update_page_context` | Merges `behdashtik_current_url` / `behdashtik_product_slug` / title / seen-at into conversation `custom_attributes` (reaches the agent in webhook payloads). Journey private notes unchanged. |
| `app/javascript/dashboard/routes/dashboard/settings/behdashtikAI/` | The **AI Assistant** settings section (tabs: Overview, Providers & Models, Prompts, Content Gaps, Responses, Handoffs & Telegram, Change Log, Test Chat). |
| `app/javascript/dashboard/routes/dashboard/settings/profile/TelegramConnection.vue` | Profile-settings panel: link/unlink Telegram, notification preference. Takes a `service` prop — rendered once per agent. |
| `app/javascript/dashboard/api/behdashtikAI.js` | Axios helper for the proxy. Default export follows the panel's `service` ref; `forService(name)` pins one agent. |
| `app/javascript/dashboard/i18n/locale/en/behdashtikAI.json` | UI strings (dashboard is English by decision; customer-facing text is Persian and lives in the AI service's prompt store). |
| Sidebar entry | `components-next/sidebar/Sidebar.vue` → Settings → "AI Assistant". |

No Rails migrations were added — all AI state lives in the service's SQLite.

## Gotchas

- `api_access_token` (underscored header) is dropped by nginx on the public
  URL; the dashboard's own auth headers are fine. For curl tests of the
  proxy use `http://127.0.0.1:4000`.
- Do not also enable Captain on inbox 1 or 12 — two bots would race for `pending`.
- `BEHDASHTIK_VISITOR_JOURNEY_ALLOWED_ORIGINS` (and every other `.env` value)
  is captured when the container is created. After editing `.env` run
  `docker compose up -d rails sidekiq`; `docker restart` keeps the old value.
- Conversation control mode lives in the AI service; setting a conversation
  back to **Pending** from the dashboard returns it to the AI.
- A card action may carry an optional `color` (hex) that overrides the widget
  colour for that one button — `shared/components/CardButton.vue`. The AI
  service uses it for the Basalam «ورود به باسلام» button (`#FF5F4A`). Fork
  customization: re-apply after a Chatwoot upgrade.
- Anything the assistant must not answer on its own (in-city delivery timing,
  which only the warehouse knows) gets its **own tool** in the AI service that
  sets the handoff itself. Telling the model in the prompt to "then call
  request_human_handoff" is not reliable — it writes the promise and skips the
  call. Same for facts the tool needs the model to state: put them in the tool's
  return value, or it deflects with "قوانین فروشگاه را مطالعه کنید".
- Same-day delivery exists only in **Qom** (the warehouse city), so
  `ask_warehouse_delivery_time` takes a `city` and hands off only for Qom —
  empty city means "ask first", another city is answered in place from the
  registered shipping terms. Qom is handed off on holidays too (a courier may
  still deliver), and the handoff reason carries the Jalali date so support has
  the context.
- The Jalali working-day calendar is `app/services/jalali.py` in the AI service
  (`jdatetime` + `iranholidays`, both offline — Iranian holiday APIs are not
  reachable from this host). It supplies today's Jalali date, whether it is a
  holiday, and the next working day.

## Chatwoot-side settings the agent depends on (2026-08-02)

These live in the Chatwoot DB, not in the service, so they survive a service
redeploy but are lost on a DB restore from before that date:

- **Inbox greeting** is enabled on inboxes 1 and 12 with the Persian capability
  list ("what I can do for you"). It is the automatic first bubble of every
  conversation; the agent is told not to repeat the list in its own reply.
- **Label `spam`** exists on account 2. The agent applies it (and hands off to
  Telegram) after three turns of a customer sending one request in fragments.

## Tests

```
spec/services/behdashtik/visitor_journey_service_spec.rb
spec/controllers/api/v1/accounts/behdashtik_ai/proxy_controller_spec.rb
```

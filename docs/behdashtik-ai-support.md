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

## Chatwoot-side code

| Path | Purpose |
|---|---|
| `app/controllers/api/v1/accounts/behdashtik_ai/proxy_controller.rb` | Authenticated pass-through `/api/v1/accounts/:id/behdashtik_ai/*` → AI service `/admin/v1/*`. Admin-only except the agent allowlist (telegram self-service, evaluation submit). Injects `X-Chatwoot-User-Id/Name`; overwrites evaluation rater identity server-side. |
| `app/services/behdashtik/visitor_journey_service.rb#update_page_context` | Merges `behdashtik_current_url` / `behdashtik_product_slug` / title / seen-at into conversation `custom_attributes` (reaches the agent in webhook payloads). Journey private notes unchanged. |
| `app/javascript/dashboard/routes/dashboard/settings/behdashtikAI/` | The **AI Assistant** settings section (tabs: Overview, Providers & Models, Prompts, Content Gaps, Responses, Handoffs & Telegram, Change Log, Test Chat). |
| `app/javascript/dashboard/routes/dashboard/settings/profile/TelegramConnection.vue` | Profile-settings panel: link/unlink Telegram, notification preference. |
| `app/javascript/dashboard/api/behdashtikAI.js` | Axios helper for the proxy. |
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

## Tests

```
spec/services/behdashtik/visitor_journey_service_spec.rb
spec/controllers/api/v1/accounts/behdashtik_ai/proxy_controller_spec.rb
```

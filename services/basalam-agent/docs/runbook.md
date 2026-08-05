# Runbook — Behdashtik AI Support Agent

## Services and locations

| Thing | Where |
|---|---|
| This service | `/root/behdashtik-ai-agent`, systemd `bdsk-ai-agent`, 127.0.0.1:8010 |
| Public URL | `https://support.behdashtik.ir/ai-agent/` (nginx location on the support vhost) |
| State DB | `/root/behdashtik-ai-agent/data/agent.db` (SQLite WAL) |
| Secrets | `/root/behdashtik-ai-agent/.env` (0600, gitignored) |
| Hub agent API (main site) | `https://mainhub.behdashtik.ir/api/agent/v1` (repo `/root/behdashtik-hub-main`, systemd `bdsk-mainhub`) |
| Hub agent API (dev site) | `https://hub.behdashtik.ir/api/agent/v1` (repo `/root/wordpress-data-hub`, systemd `bdsk-devhub`) |
| Chatwoot | containers `chatwoot-rails-1` / `chatwoot-sidekiq-1`, repo `/root/tesnet-behdashtik/chatwoot` |
| Admin UI | Chatwoot → Settings → **AI Assistant** (admins only) |
| Operator Telegram | Chatwoot → Profile Settings → **Telegram Connection** |
| Telegram bot | @behdsahtiksupportbot (webhook mode) |
| Logs | `journalctl -u bdsk-ai-agent -f` |

## Daily operations

- **Disable AI globally**: dashboard Overview → toggle, or
  `curl -X PATCH -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d '{"values":{"ai_enabled":false}}' http://127.0.0.1:8010/admin/v1/settings`
- **Change model / prompt / fallback / traffic split**: dashboard tabs — takes
  effect on the next message, no restart.
- **Pause one conversation**: dashboard Handoffs → Return to AI toggles;
  per-conversation pause via `POST /admin/v1/conversations/{id}/pause`.
- **Restart**: `systemctl restart bdsk-ai-agent` (queued jobs recover; jobs
  older than 30 min are abandoned into a human handoff).
- **DB backup**: `sqlite3 data/agent.db ".backup data/agent-$(date +%F).db"`.
- **Prompt cost controls** (all in code, no config): system prompt kept lean
  (`default_prompts.SYSTEM_MAIN`, currently ~2.7k chars — check
  `SELECT version, length(content) FROM prompt_versions WHERE prompt_key='system_main'`
  before growing it), product blocks capped at `tools.DESCRIPTION_LIMIT`,
  validator evidence at `nodes.EVIDENCE_BUDGET`, replayed history at
  `runner.HISTORY_MESSAGE_CHARS` / `HISTORY_TOTAL_CHARS`. Per-call token counts
  live in `responses.input_tokens` — compare there before and after any change.
- **Who gets handoff alerts**: every operator row in `telegram_operators` that is
  `active` with `pref` in (`telegram`, `both`). Each agent/admin links their own
  account from Profile Settings → Telegram Connection; an unlinked agent gets
  nothing on Telegram and only sees the conversation in the Chatwoot open queue.

## Verification scenarios (all verified working on DEV)

Send through the widget on dev.behdashtik.ir (or create messages via
`rails runner` — see the spec/commit history):

1. **Product by page context**: open a product page, ask «این محصول موجوده؟»
   → agent resolves the slug from journey custom_attributes and answers with
   live stock/price/variants.
2. **Product search**: «کرم پودر دوسه موجوده؟» → normalized word-AND on the
   title. «برای موهای چرب چی دارید؟» → the title pass finds too little, so the
   Hub widens into short + full descriptions (phrase matches ranked first) and
   every hit comes back with `[موجود]`/`[ناموجود]`, price and a ~150-char
   description snippet — the agent picks from that instead of fetching each
   product in full.
3. **Order status**: «سفارشم کجاست؟ شمارم ۰۹...» → deterministic phone
   verification against Hub, then status + tracking code in Persian.
4. **Expiry**: «تاریخ انقضای این محصول چیه؟» on a product whose description
   or attributes carry a date/shelf-life (≈214 products do) → the agent
   answers with the registered value. On a product with nothing registered →
   honest reply + a Content Gap row in the dashboard.
5. **Off-topic**: «نتیجه بازیپرسپولیس؟» → polite Persian refusal.
6. **Human request**: «میخوام با اپراتور صحبت کنم» → status open + Telegram
   fanout with Claim button; exactly one claimer wins; replies (reply-to in
   Telegram) land in the right conversation; Return to AI resumes the agent.
7. **Voice**: send a Persian voice note → transcript appears as a private
   note, then a normal grounded answer.
8. **Duplicate events**: replay a signed webhook → `duplicate: true`, single
   reply (also `scripts/smoke_webhook.py`).
9. **Shipping cost**: «هزینه ارسال به قم چقدره؟» → 90,000 tomans; any other
   city → the 115,000 base; orders above 2,000,000 → free. The numbers come
   from the **`store_facts` prompt key** (dashboard → AI Assistant → Prompts),
   not from WordPress — the store has no shipping page. Edit that prompt to
   change prices; a new version applies on the next message, no restart.
10. **Product cards**: «یه استیک ضد تعریق مردانه معرفی کن» → a native Chatwoot
    `cards` message (photo, price, «افزودن به سبد»، «جزئیات محصول») **followed by**
    the text answer, which describes what was sent without repeating names or
    prices (the cards already show them) and offers more on request.
    `nodes._needs_cards` enforces this: if a product answer comes back with no
    cards, the tool loop is nudged once to call `show_product_cards` and rewrite
    the text — the model otherwise lists products by name instead of carding them.
    Products already carded in the conversation are listed to the model in the
    `already_shown_products` block, so «چیز دیگه‌ای هم داری؟» triggers a fresh
    search instead of a re-pitch of the same items.
    **Cards are queued, not sent, by `show_product_cards`.** They live in
    `state["cards"]` and only leave through `runner._send_cards` once validation
    accepts the draft; a rejected draft has its cards dropped and the rewrite
    builds its own. Sending from inside the tool used to leave the rejected
    attempt's cards on the customer's screen and stack a second set on top
    (conversation 149, 2026-08-03: four cards for one «بسته کاملشم دارید؟»).
    Each queued card carries `_ids` — the Hub id *and* the Basalam id — and both
    are persisted to `conversations.shown_products`, because the model names
    whichever id it happens to have; deduping on one space alone re-sent products.
    The tool also emits a `shown_cards` data block: the draft is written from the
    cards, so without that evidence the validator judged a correct answer
    ungrounded and forced the rewrite that produced the contradiction.
    The add-to-cart button is a *postback*: the widget raises a `chatwoot:postback`
    window event, `wp-plugin/js/behdashtik-cart.js` turns it into a WooCommerce
    AJAX add-to-cart, and the theme's own mini-cart opens. Variable products get a
    «انتخاب مدل و خرید» link instead — the model has to be picked on the page.
    Out-of-stock products are never carded.
11. **Cart from the chat**: «چی تو سبدمه؟» → `get_my_cart` answers from the
    snapshot the site pushed, and sends a card per item with «یکی بیشتر» /
    «حذف از سبد» plus a summary card with «مشاهده سبد خرید» / «تسویه حساب».
    Every mutation goes through the **WooCommerce Store API** in the visitor's
    own browser (`/wp-json/wc/store/v1/cart`, nonce read from the GET response
    header), then reports to `POST /api/v1/behdashtik/cart_events`, which
    updates `custom_attributes.behdashtik_cart` and posts the success/error
    notice. WooCommerce's own Persian error text is shown verbatim — including
    the YITH min/max plugin's limits (e.g. product 4461 is capped at 1 per
    order via `_ywmmq_product_maximum_quantity`).
    Requires `BEHDASHTIK_CART_SYNC_ENABLED=true` in the Chatwoot `.env`.
    The agent can never touch a cart server-side: the cart lives in the
    shopper's WooCommerce session, so the browser is the only actor.
12. **Gift flow**: «دنبال هدیه می‌گردم» → the agent introduces the «مناسب هدیه»
    collection and asks the budget, then `suggest_gifts(budget_max)` queries that
    tag with the budget + in-stock filters, drops everything already shown, and
    picks 3 from *different* categories. That tag is the **only** source of gift
    suggestions: when it has nothing left in that budget the agent says so and
    invites a higher budget — it never falls back to the rest of the catalogue,
    because outside the tag nothing is gift-ready. Saying «خوشم نیومد» or naming a new
    budget calls it again and returns different products — `conversations.shown_products`
    is the memory, and it also stops `show_product_cards` from re-sending a card.
13. **Greeting / vague message**: «سلام» or «؟» → no tools, just a short
    capability list (find products, price/stock/usage, cart to checkout, order
    tracking, shipping rules) and a question about what they need. The same list
    is the inbox **greeting message** (Chatwoot → Settings → Inboxes → 1 and 12),
    so it is the automatic first bubble of every conversation; out-of-scope
    questions get it too via the `msg_refusal` prompt. A message that does state a
    need skips the list entirely.
    **The greeting itself is once a day.** `runner._greeted_today` checks whether
    any outgoing or template bubble already went to this customer today in Tehran
    time (the inbox's automatic welcome counts), and `agent_node` injects either
    `GREETING_ALLOWED` or `GREETING_ALREADY_DONE` right after the customer's
    message — the same late position the fragmented note uses, because standing
    instructions placed earlier get dropped. It is decided in Python, not left to
    the model: the history it sees carries no clock, so it cannot tell today's
    turns from last week's, and «سلام… وقت بخیر» was landing three times inside
    one live thread. Once greeted, a bare «سلام» later that day is answered with
    «در خدمتم؛ دنبال چه محصولی هستید؟» instead of a greeting back.
14. **Reply to a bubble**: reply to a product card and ask «تاریخ انقضاش کیه؟» →
    `content_attributes.in_reply_to` is resolved back to that message (card titles
    or text) and injected as the `reply_context` block, so the agent looks that
    product up instead of asking which one. Also fed to the intent router, so a
    bare follow-up still routes to `product`.
15. **Fragmented messages**: a turn that arrives as several bubbles gets a normal
    answer plus a gentle "please send your whole request in one message". After
    `runner.FRAGMENT_LIMIT` (3) such turns, `conversations.fragmented_turns` trips
    the escalation: the conversation is labelled **spam** in Chatwoot, the customer
    gets `msg_handoff`, and the handoff fans out to Telegram (`reason_kind="spam"`)
    for an operator to claim. The counter is cumulative per conversation and never
    resets on its own.
16. **Usage limit**: covered by `test_graph_tells_customer_when_usage_limit_is_hit`;
   check remaining credit with
   `curl -H "Authorization: Bearer $KEY" https://openrouter.ai/api/v1/key`.

## Failure modes

| Failure | Behavior |
|---|---|
| OpenRouter primary model down | 1 retry → fallback model → else handoff with Persian apology |
| OpenRouter returns a **partial** completion | seen live 2026-08-03: HTTP 200, `finish_reason: "error"`, zeroed usage and text cut mid-sentence (a reply arrived as just «برای»). `llm._check_complete` raises `LLMTruncated`, the same model is retried once, then the fallback model runs. Symptom before the fix: half-sentence replies to customers, `Invalid JSON: EOF while parsing` from structured roles, and doubled latency from the validation revision loop |
| OpenRouter usage limit / 429 / out of credit | detected as `LLMQuotaExceeded` → customer is told the assistant hit its usage limit (`msg_limit_reached`) and the chat is handed to a human; Telegram notification is labelled ⚠️ AI usage limit reached |
| Reply fails validation twice | escalates to a human for order questions, for a **product** draft that tells the customer we don't stock something (`nodes.UNAVAILABILITY_CLAIMS`), or when the draft had no tool data behind it (`nodes._must_escalate`); otherwise the draft is delivered and the verdict is kept in `responses.validation` for review — a validator quibble must not dead-end a browsing customer, but a wrong «نداریم» loses the sale and can't be walked back. Product intent only: «تحویل حضوری نداریم» is a policy answer, and escalating those sent a quarter of them to an operator for nothing |
| Operator answers the thread | any outgoing bubble that is not ours (`bslm:bot-…`, the agent bot, or a Telegram-relayed reply) flips the conversation to `human` **and it stays there** — `handoff.taken_over_by_operator`. It used to be a 30-minute activity window that expired mid-conversation: an operator handled a request at 17:25 and 17:58, the customer wrote back at 19:14, and the agent cut in with its own out-of-scope notice (conversation 164, 2026-08-03) |
| Handing the thread back | an operator types **`ai`** on its own, in Chatwoot or in the Basalam panel. The bridge refuses to forward that word to Basalam (checked before any lookup, so it can never reach a customer) and the agent deletes the Chatwoot bubble and calls `handoff.return_to_ai`. Basalam has no delete/edit message API — `DELETE/PATCH/PUT /v1/chats/{id}/messages/{id}` answer with a plain-text `404 page not found` while real routes answer `{"message":"not found"}` — so a word typed in the Basalam panel is already delivered and stays in the customer's thread; only the mirrored Chatwoot bubble goes away |
| Product authenticity | `tools.is_genuine` looks for «اصل»/«اصلی» as a whole word in the Hub name or the Basalam title (a substring match made «فاصله» and «اصلاح» read as authenticity claims), and `rating`/`review_count` come over from Basalam, which is the only side that has them. Both land in `format_product` as their own lines, and `nodes._needs_authenticity` forces one rewrite when the customer asked and the answer dodged |
| Hub down | tools return "unavailable" → honest reply + handoff for order queries |
| Basalam catalogue unreachable (bridge slow/down) | `basalam_client.match_titles` raises `CatalogUnavailable`; product tools return the `CATALOG_DOWN` notice, so the agent says a technical problem occurred and hands off. It must **never** be reported as «نداریم» — on 2026-08-03 a 16 s in-request catalogue refresh timed out `/products/match` and a customer was told we had no کرم پودر while three were in stock. The bridge now serves the stale catalogue and refreshes in a background thread |
| This service down | Chatwoot agent-bot retries exhaust → conversation auto-opens for humans |
| Telegram down | handoff still opens in Chatwoot; fanout errors logged, non-fatal |
| No operator linked to Telegram | fanout reaches nobody. `initiate` now logs `reached NO telegram operator` at ERROR — grep for it: the customer has already been promised a human. The Qom warehouse ask (`reason_kind=qom_dispatch`) is the worst case, since the customer was told «موضوع رو به انبار اطلاع دادم» |
| Chatwoot down | jobs retry with backoff; nothing lost (persisted queue) |
| Restart mid-processing | queued/running jobs re-queued on boot; stale ones → handoff |

## Latency budget (measured 2026-08-02)

A turn costs roughly `debounce + LLM + HTTP`. From `responses` telemetry, median
per turn: intent_routing ~0.5s, main_response ~2s (2 passes: tools, then text),
validation ~0.4s. Four things were tuned; re-check them before blaming the models:

| Lever | Setting / code | Note |
|---|---|---|
| `debounce_seconds` **4 → 1.5** | dashboard setting, hot | a flat wait on *every* reply; the single biggest win. Below ~1s, rapid multi-bubble messages stop merging and the fragmented-message counter under-counts |
| validation model **flash → flash-lite** | `role_assignments.validation` (primary 2, fallback 1) | halves validation latency; the validator only matches claims against tool data |
| `product_search` caching | `hub_client`, `PRODUCT_TTL` (10 min) | was uncached at ~250-350ms per call; `invalidate("product:")` from the Hub webhook clears it, so stock stays ≤60s fresh |
| pooled HTTP clients | `hub_client._clients`, `chatwoot_client._clients` | one `AsyncClient` per hub / per token instead of one per call (a TLS handshake each). Closed in the app lifespan |

Result: a normal turn is ~4-6s end to end (was ~9s). **Redis was considered and
rejected** — the hub cache is local SQLite reads (sub-ms), so it is not the
bottleneck and Redis would only add a failure surface. What remains is LLM time.

Outliers are almost always a **validation failure + revision** (double the passes)
or an OpenRouter fallback to `gpt-5-mini` (~7s); one live test turn hit both and
took 24s. Check `responses` for the message id before treating a slow turn as
systemic.

**A rejected draft takes its cards with it.** Cards are built during the tool loop
but only sent after validation, so a draft that fails and gets rewritten would
otherwise leave the customer with cards the new answer no longer stands behind —
and since cards go out first, they would contradict the text that follows.
`validate_node` therefore clears `state["cards"]` on a revision and rolls
`shown_products` back to `shown_baseline` (captured at the start of the run, so
products carded in *earlier* turns stay suppressed). The rollback is what lets the
retry re-card the same products; without it the dedupe would silently drop them
and the customer would read "چند مورد براتون فرستادم" with nothing attached.

## Schema changes

`db.init_db()` runs `create_all`, which creates missing *tables* but never adds a
column to an existing one. A new column on a live DB needs the `ALTER TABLE` by
hand, then a restart:

```bash
cd /root/behdashtik-ai-agent
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/agent.db'); \
c.execute('ALTER TABLE conversations ADD COLUMN fragmented_turns INTEGER NOT NULL DEFAULT 0'); c.commit()"
systemctl restart bdsk-ai-agent
```

Applied so far: `conversations.fragmented_turns` (2026-08-02).

## Rollback

1. Dashboard AI toggle off (agent acks, stays silent).
2. `systemctl stop bdsk-ai-agent` — Chatwoot flips pending conversations to
   open on webhook failure; humans take over.
3. Detach the bot entirely:
   `docker exec chatwoot-rails-1 bundle exec rails runner 'AgentBotInbox.find_by(inbox_id: 1)&.destroy'`
   — the inbox reverts to fully human (new conversations start `open`).
4. Telegram: `curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`.
5. Chatwoot UI changes: `git revert` on the repo →
   `docker exec chatwoot-rails-1 sh -c "cd /app && RAILS_ENV=production NODE_ENV=production bin/vite build"`
   → restart containers. No Rails migrations were added.
6. Hub: revert the `agent_api` blueprint registration in
   `server2/dashboard.py`; `systemctl restart bdsk-devhub`.

## Two sites, two hubs (live since 2026-07-29)

The service answers both stores from one process. Which store a conversation
sees is decided by its **Chatwoot inbox**, recorded in `conversations.inbox_id`
from the webhook payload:

| Site | Chatwoot inbox | Website token | Data hub | Cache namespace |
|---|---|---|---|---|
| behdashtik.ir (main) | 12 — "Behdashtik" | `TxEMJz9MYj4NegshY7zf45ff` | `mainhub.behdashtik.ir` | `main:` |
| dev.behdashtik.ir | 1 — "Behdashtik-Dev" | `jTB44Yk8rGv69nqVBy3anb9h` | `hub.behdashtik.ir` | `dev:` |

`config.Settings.hub_for_inbox` maps inbox → (namespace, base URL, key);
`runner.handle_messages` calls `hub_client.use_inbox()` once per run and every
request and cache key below it follows that choice. `BDSK_AI_MAIN_INBOX_ID`
turns the whole thing on — unset it and everything falls back to the dev hub.
Dashboard Test Chat answers from the **main** hub unless given `inbox_id`.

Sanity check after any change here: `GET /admin/v1/health` reports `hub` and
`main_hub` separately, and
`SELECT DISTINCT substr(key,1,4) FROM hub_cache` should show both namespaces
once both sites have been used.

### WordPress side

The plugin (`chatwoot-plugin` 1.3.0, built to `wp-plugin/dist/`) is installed on
both sites and picks its own website token from `home_url()`, so no per-site
configuration is needed. It always enqueues the journey tracker; Chatwoot is
what gates it, via `BEHDASHTIK_VISITOR_JOURNEY_ALLOWED_ORIGINS` in the Chatwoot
`.env` (now `dev.behdashtik.ir`, `behdashtik.ir`, `www.behdashtik.ir`). That
env var is baked into the container at creation — `docker compose up -d rails
sidekiq` is required, `docker restart` silently keeps the old value.

## Silent-bot triage

If the bot stops replying in one conversation, it is almost always a handoff,
not an outage — `conversations.mode` is `human`, so the AI stays silent by
design until someone returns it. Check:

```bash
sqlite3 data/agent.db "SELECT id, conversation_id, reason_kind, status FROM handoffs ORDER BY id DESC LIMIT 5;"
```

Return it to the AI from the dashboard (Handoffs → Return to AI) or the
Telegram console button. **Handoff notifications only reach operators who
linked Telegram** (Profile Settings → Telegram Connection); with none linked
the conversation still appears as `open` in Chatwoot, but nobody is paged.

Second, expected cause on the Basalam inbox: **an operator answered from the
Basalam panel in the last 30 minutes**. Those replies never touch Chatwoot's
status or assignee, so `mode` stays `ai` and the bot used to talk over the
operator. It now stays quiet for `OPERATOR_ACTIVE_SECONDS` (30 min, in
`app/agent/runner.py`) after any mirrored outgoing message and resumes on its
own — no state is stored, nothing to reset. The bridge tags the agent's own
product cards `bslm:bot-…` so they never trigger the silence.

### Tool-use nudges (`app/agent/nodes.py`)

Some rules never held on prompt wording alone — the model answered from memory
instead of calling the tool — so the agent loop re-prompts once per kind
(`NUDGES`, at most one nudge of each per turn):

| Kind | Fires when | Fix it forces |
|---|---|---|
| `restock` | the message asks when something is restocked and `ask_restock_date` was not called | call the tool instead of promising a follow-up |
| `search` | a product answer claims «داریم»/«نداریم» with no search behind it | run `search_products` first |
| `cards` | new products named in the text but never carded | send the cards, drop names/prices from the text |
| `authenticity` | «اصل هست؟» with an «اصالت» line in the data but no answer in the draft | answer the actual question first |
| `stockout` | the draft says «ناموجود» but no tool row shows `[ناموجود]` | search first; only claim it from the data |
| `empty` | the draft came back empty although a tool succeeded | write the one or two sentences instead of sending nothing |

Prompt-only wording is still tried first; add a nudge only when a rule is
observed failing repeatedly in real replies.

**Match the customer's words, not the forwarded card.** Basalam customers open by
forwarding a product card, and its text is part of `state["text"]`. A trigger that
matches the whole message matches the *card*: the bare «✅ موجود» on every card
looked like a restock question (2026-08-04, conversation 173 — an in-stock product
was declared «ناموجود» and the chat escalated), and «... اصلی» in a title, which
half the catalogue carries, looked like «اصل هست؟». Every trigger runs on
`_customer_words(text)`, which drops the card lines. Keep new ones on it too, and
keep the patterns question-shaped rather than keyword-shaped.

### Order tracking (live since 2026-08-04)

The customer never gets asked for an order number or a phone: the Basalam chat
already proves who they are. `get_my_order` (no arguments) calls the bridge's
`GET /orders?conversation_id=…`, which maps conversation → chat → contact →
Basalam user id and reads `GET /v1/vendor-parcels` (scope `vendor.parcel.read`).
The graph has **no order-auth node** any more — that WooCommerce path was the
source of the «شمارهٔ سفارش‌تون رو بفرستید» dead end.

The bridge deliberately strips the recipient's address, postal code and mobile
from what it returns; the only phone that ever reaches the customer is the
courier's, and only for پیک موتوری (on پست that field holds our own or the
customer's number, never a courier's).

Reply rules per status live in `tools.PARCEL_GUIDANCE`:

| Status | What the agent says |
|---|---|
| در حال آماده‌سازی | packed within a day and handed to the post counter; no tracking code promised |
| ارسال شده (پست) | tracking code + link, arrival expected in 2–4 days |
| ارسال شده (پیک موتوری) | dispatched + the courier's phone; asks the customer to confirm delivery |
| تحویل شده / رضایت مشتری | invites a review and sends Basalam's own «ثبت تجربهٔ خرید» cards |

**Delivered but the customer says it never arrived**: `report_delivery_problem`
sends a Telegram *alert* (`telegram_bot.alert_admins`) — an alert, not a handoff,
so the agent stays in the conversation and gives the 193 advice itself.

Review cards are real Basalam `review` messages, one per order item
(`entity_id` = order **item** id, not product id). The bridge keeps a
`review_sent` table so an item can never be carded twice.

### Restock questions («کی شارژ می‌شود؟»)

Restock dates exist in no data source, so the agent never answers them from the
product text. `ask_restock_date` (app/agent/tools.py) hands the conversation to a
human with `reason_kind=restock`, and the customer is told the answer will come
back into this chat. The Telegram card names the product, so the operator can ask
the warehouse and reply in Chatwoot. Same shape as the Qom dispatch ask.

The tool **verifies stock before it accepts the premise** — it once declared an
in-stock product gone and escalated. Three outcomes:

1. the product is in stock → no handoff; the model is told to say so and invite the order
2. that listing is sold out but another listing of the same item is in stock → no
   handoff; card the available listing and say plainly that the forwarded one is gone
3. nothing available → handoff, with a `وضعیت موجودی: ناموجود` line as the evidence
   the stock-out guard and `_must_escalate` look for

### Never say «ناموجود» without evidence

Telling a customer we don't have something we do costs the sale outright, so the
claim is checked in three independent places:

- `ask_restock_date` verifies against the Basalam catalogue (above)
- the `stockout` nudge rejects any draft claiming unavailability with no `[ناموجود]`
  row behind it — this also covers handoff drafts, which never reach the validator
  (`route_after_agent` sends a handoff straight to `finalize`)
- the bridge computes `available` itself: Basalam's own `is_available` is `True`
  even for a sold-out listing (inventory 0, `is_saleable` false), so
  `_product_brief` requires published + saleable + inventory > 0, the same rule
  the catalogue uses

Conversely, a *correct* stock-out claim must not be thrown away: `_must_escalate`
only escalates an unavailability answer when the tool data does **not** back it.
Availability markers stay in one notation everywhere — `[موجود]` / `[ناموجود]`,
including per-variant rows — because both the model and the validator ignored the
English `outofstock`.

### The validator escalates too

A rejected reply becomes a handoff, so a validator mistake costs a customer. Two
of its blind spots are handled in the `validate` prompt rather than in code:
Persian vs Latin digits (`۰۴` = `04`, `۵۷۹٬۰۰۰ تومان` = `579,000`) and the fact
that availability belongs to a *listing id*, so "the one you sent is sold out but
we have this other one" is grounded, not a contradiction.

## Known limitations

- There is no structured expiry/batch field in WooCommerce: expiry lives as
  prose in product descriptions/attributes. The agent extracts it from there
  (see `extract_longevity_info`); products with no mention at all still get an
  honest refusal + Content Gap. A per-batch expiry answer would need a real
  inventory field upstream.
- Embeddings/vector retrieval role is defined but disabled (OpenRouter has no
  embeddings endpoint; 395 structured products don't need it yet).
- Operator voice replies from Telegram are not forwarded (text, photos and
  documents are).
- STT quality depends on the configured audio model (default Gemini Flash);
  the model is swappable in the dashboard (role `stt`).

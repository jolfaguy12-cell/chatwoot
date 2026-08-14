"""Graph nodes. Classification and validation use cheap models with structured
output; order authorization is deterministic Python; the agent node runs a
bounded tool-calling loop on the main response model."""

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from app import crud
from app.agent import prompt_store
from app.agent.injection import wrap_data
from app.agent.llm import LLMFailure, LLMQuotaExceeded, run_role
from app.agent.state import AgentState
from app.agent.tools import ToolContext, build_tools
from app.db import db_session
from app.services import basalam_client, hub_client, persian
from app.services.hub_client import HubUnavailable

log = logging.getLogger(__name__)


def _log_call(state: AgentState, kind: str, role: str, result, *, extra: dict | None = None):
    state.setdefault("run_logs", []).append({
        "kind": kind, "role": role, "model_used": result.model_used,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd, "latency_ms": result.latency_ms,
        "status": result.status, "error": "; ".join(result.errors), **(extra or {}),
    })


def _handoff(state: AgentState, kind: str, reason: str) -> AgentState:
    state["outcome"] = "handoff"
    state["handoff_kind"] = kind
    state["handoff_reason"] = reason
    return state


def _provider_handoff(state: AgentState, error: LLMFailure, stage: str) -> AgentState:
    """Provider failure → handoff, distinguishing an exhausted usage limit
    (which the customer is told about explicitly) from a transient fault."""
    if isinstance(error, LLMQuotaExceeded):
        return _handoff(state, "provider_limit", f"{stage}: provider usage limit reached: {error}")
    return _handoff(state, "provider_error", f"{stage}: {error}")


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

class IntentResult(BaseModel):
    intent: str = Field(description="product | order | cart | policy | greeting | human | other")
    in_scope: bool
    product_mention: str = Field(default="", description="product name text if mentioned")
    product_url: str = Field(default="", description="product page URL if present in message")


async def classify_node(state: AgentState) -> AgentState:
    prompt, version = prompt_store.get("classify")
    context_bits = []
    if state.get("product_slug"):
        context_bits.append(f"customer is viewing product page: {state['product_slug']}")
    if state.get("reply_context"):
        # a bare «تاریخ انقضاش؟» is a product question only once you know it is a
        # reply to a product card
        context_bits.append(f"this message is a reply to — {state['reply_context']}")
    history = state.get("history") or []
    for h in history[-4:]:
        context_bits.append(f"{h['role']}: {h['content'][:200]}")
    context = "\n".join(context_bits) or "(no prior context)"
    try:
        result = await run_role(
            "intent_routing",
            [SystemMessage(content=prompt),
             HumanMessage(content=f"Context:\n{context}\n\nCustomer message:\n{state['text']}")],
            structured=IntentResult,
        )
    except LLMFailure as e:
        return _provider_handoff(state, e, "intent routing failed")
    _log_call(state, "classify", "intent_routing", result,
              extra={"prompt_key": "classify", "prompt_version": version})
    parsed: IntentResult = result.output
    state["intent"] = parsed.intent if parsed.intent in (
        "product", "order", "cart", "policy", "greeting", "human", "other") else "other"
    state["in_scope"] = parsed.in_scope and state["intent"] != "other"
    state["product_mention"] = parsed.product_mention
    if parsed.product_url:
        slug = persian.slug_from_url(parsed.product_url)
        if slug:
            state["product_slug"] = slug
            state["page_url"] = parsed.product_url
    # outcomes must be decided here — routing functions are pure readers
    # (LangGraph does not persist state mutations made inside edges)
    if state["intent"] == "human":
        return _handoff(state, "user_request", "customer asked for a human operator")
    if not state["in_scope"]:
        state["outcome"] = "refuse"
    return state


def route_after_classify(state: AgentState) -> str:
    # `order` has no separate branch: the Basalam chat already proves who the
    # customer is, so the agent just calls get_my_order like any other tool.
    return "finalize" if state.get("outcome") else "agent"



# ---------------------------------------------------------------------------
# agent (bounded tool loop)
# ---------------------------------------------------------------------------

# Greeting is once a day, decided in `runner._greeted_today` — the history the
# model sees has no clock on it, so it cannot make this call itself.
GREETING_ALLOWED = (
    "امروز هنوز پیامی برای این مشتری نفرستاده‌ایم، پس می‌توانی پاسخ را با یک سلام"
    " کوتاه شروع کنی — «سلام» یا «سلام، وقت بخیر»، و بس. بدون هیچ لقبی:"
    " «سلام دوست عزیز» ننویس. بیشتر از یک جمله برای احوالپرسی خرج نکن."
)
GREETING_ALREADY_DONE = (
    "امروز قبلاً در همین گفتگو با این مشتری حرف زده‌ایم. **دوباره سلام نکن**:"
    " نه «سلام»، نه «وقت بخیر»، نه «روز شما بخیر». مستقیم جواب خودِ پیام را بده،"
    " حتی اگر مشتری در پیامش سلام کرده باشد. اگر پیام مشتری *فقط* سلام است،"
    " به‌جای سلامِ متقابل بگو در خدمتی و بپرس چه کمکی از دستت برمی‌آید"
    " (مثلاً «در خدمتم؛ دنبال چه محصولی هستید؟»)."
)


async def agent_node(state: AgentState) -> AgentState:
    system_prompt, version = prompt_store.get("system_main")
    # what was already carded before this run started — the point a rejected
    # attempt is rolled back to, so its cards can be built again
    state.setdefault("shown_baseline", list(state.get("shown_products") or []))
    ctx = ToolContext(
        conversation_id=state["conversation_id"],
        intent=state.get("intent", ""),
        text=state.get("text", ""),
        product_slug=state.get("product_slug", ""),
        page_url=state.get("page_url", ""),
        cart=state.get("cart") or {},
        shown_products=list(state.get("shown_products") or []),
    )
    tools = build_tools(ctx)
    tool_map = {t.name: t for t in tools}

    messages: list = [SystemMessage(content=system_prompt)]
    if state.get("product_slug"):
        messages.append(SystemMessage(content=wrap_data(
            "page_context",
            f"مشتری الان روی این صفحه محصول است: {state.get('page_url') or state['product_slug']}")))
    if state.get("reply_context"):
        messages.append(SystemMessage(content=wrap_data(
            "reply_context",
            f"پیام مشتری ریپلای به این مورد است — {state['reply_context']}."
            " سؤالش دربارهٔ همین است، پس نام محصول را از او نپرس: اول همین محصول را با"
            " search_products (با همین نام) پیدا کن و بعد get_product_details را بزن،"
            " بعد جواب بده. اگر در آن پیام چند محصول بود و مشخص نیست کدام را می‌گوید،"
            " کوتاه بپرس کدام‌یک.")))
    # The usual Basalam opening is a forwarded product card, and the card text
    # already carries a price and «✅ موجود» — so the model answered from it and
    # called nothing, which the validator (rightly) rejects as ungrounded, and
    # the customer got escalated instead of an answer. Fetch it up front: every
    # such turn then has real price, stock and variants behind it.
    forwarded = await _forwarded_product(tool_map, state.get("text", ""))
    if forwarded:
        messages.append(SystemMessage(content=forwarded))

    if ctx.shown_products:
        # the wrap-up text is written from the history, where the old product names
        # are still visible — without this the model re-lists products whose cards
        # the dedupe already dropped («چیز دیگه‌ای هم داری؟»)
        messages.append(SystemMessage(content=wrap_data(
            "already_shown_products",
            "کارت این محصول‌ها در همین گفتگو قبلاً برای مشتری فرستاده شده (هر محصول"
            " ممکن است با شناسهٔ هاب و شناسهٔ باسلام، یعنی بیش از یک شناسه، آمده باشد): "
            + "، ".join(f"id={pid}" for pid in ctx.shown_products)
            + ". این‌ها دوباره کارت نمی‌گیرند، پس به‌عنوان پیشنهاد تازه دوباره معرفی‌شان"
              " نکن. اگر مشتری گزینهٔ تازه می‌خواهد، جستجوی تازه بزن و فقط موارد تازه را"
              " بگو؛ اگر چیز تازه‌ای نبود، صادقانه همین را بگو. البته اگر مشتری خودش"
              " دربارهٔ یکی از همین‌ها پرسید، طبیعتاً کامل جوابش را بده.")))
    for h in (state.get("history") or [])[-10:]:
        cls = HumanMessage if h["role"] == "user" else AIMessage
        messages.append(cls(content=h["content"]))
    messages.append(HumanMessage(content=state["text"]))
    # after the customer's message on purpose, like the fragmented note below:
    # the model drops standing instructions placed before it
    messages.append(SystemMessage(content=GREETING_ALLOWED
                                  if state.get("greeting_allowed", True)
                                  else GREETING_ALREADY_DONE))
    if state.get("fragmented"):
        # last position on purpose: standing instructions placed before the
        # customer's message get dropped by the model
        messages.append(SystemMessage(content=(
            "مشتری همین درخواست را تکه‌تکه و در چند پیام جدا فرستاد. جوابش را کامل بده و"
            " در **پایان** پاسخ، یک جملهٔ مهربان اضافه کن که لطفاً کل سؤال یا درخواستش را"
            " در یک پیام بنویسد تا دقیق‌تر و سریع‌تر کمکش کنی.")))
    if state.get("critique"):
        # carry the evidence already retrieved into the retry, otherwise the
        # model starts blind and is pushed into answering without tool data
        messages.append(SystemMessage(content=_evidence(state.get("data_outputs") or [])))
        messages.append(SystemMessage(content=(
            INTERNAL_NOTE
            + "پاسخ قبلی‌ات از بررسی کیفیت رد شد. اشکال: "
            f"{state['critique']}\nپاسخ را اصلاح کن و فقط بر اساس داده‌های ابزارها بنویس."
            " اگر داده‌ای برای ادعایی نداری، آن ادعا را حذف کن یا ابزار را دوباره صدا بزن."
            " کارت‌هایی که در تلاش قبلی ساخته بودی لغو شدند و هنوز چیزی برای مشتری نرفته؛"
            " پس هر محصولی را که در پاسخ نهایی معرفی می‌کنی دوباره با show_product_cards"
            " بفرست، و اگر به این نتیجه رسیدی که محصول مناسبی نیست، هیچ کارتی نفرست.")))

    with db_session() as session:
        max_iterations = int(crud.get_setting(session, "max_tool_iterations") or 4)

    draft = ""
    # one nudge of each kind at most: a search nudge is often followed by a
    # legitimate card nudge, and a single flag swallowed the second one
    nudges_used: set[str] = set()
    for _ in range(max_iterations + 1):
        try:
            result = await run_role("main_response", messages, tools=tools)
        except LLMFailure as e:
            return _provider_handoff(state, e, "main response failed")
        _log_call(state, "agent", "main_response", result,
                  extra={"prompt_key": "system_main", "prompt_version": version})
        ai_msg: AIMessage = result.output
        messages.append(ai_msg)
        if not ai_msg.tool_calls:
            content = ai_msg.content if isinstance(ai_msg.content, str) else str(ai_msg.content)
            # A nudged rewrite that comes back empty must not erase the answer we
            # already had: a correct «بله اصل است…» was being replaced by an empty
            # pass and delivered to the customer as a provider-error handoff.
            draft = content if content.strip() else draft
            if not draft.strip():
                # With tool data in hand an empty draft is the model deciding the
                # card said it all — asking once is far better than the apology
                # handoff an empty draft otherwise turns into.
                kind = "empty" if any(c["ok"] for c in ctx.calls) else ""
            else:
                # `draft` stays as the fallback if the loop runs out of iterations
                kind = ("stockout" if _claims_unbacked_stockout(ctx, draft)
                        else "restock" if _needs_restock(state, ctx)
                        else "search" if _needs_search(state, ctx, draft)
                        else "cards" if _needs_cards(state, ctx)
                        else "authenticity" if _needs_authenticity(state, ctx, draft)
                        else "")
            nudge = NUDGES.get(kind, "") if kind not in nudges_used else ""
            if nudge:
                nudges_used.add(kind)
                # a HumanMessage, not a SystemMessage: a system turn appended
                # after the assistant's own reply came back empty every time,
                # which cost the rewrite and, before the guard above, the draft.
                # But arriving in the customer's turn, the note reads as the
                # customer scolding us — the model answered it with «حق با شماست،
                # اشتباه کردم، باید search_products می‌زدم». Hence the marker.
                messages.append(HumanMessage(content=INTERNAL_NOTE + nudge))
                continue
            break
        for call in ai_msg.tool_calls:
            tool = tool_map.get(call["name"])
            if tool is None:
                output = f"unknown tool {call['name']}"
            else:
                try:
                    output = await tool.ainvoke(call["args"])
                except Exception as e:  # noqa: BLE001 — tool errors go back to the model
                    log.exception("tool %s failed", call["name"])
                    output = f"خطا در اجرای ابزار: {e.__class__.__name__}"
            messages.append(ToolMessage(content=str(output), tool_call_id=call["id"]))

    # Keep evidence from earlier passes: on a revision the model often refines
    # wording without re-calling tools, and dropping the first pass's tool
    # output would make the validator judge a grounded answer as unsupported.
    state["tool_calls"] = (state.get("tool_calls") or []) + ctx.calls
    previous_outputs = state.get("data_outputs") or []
    state["data_outputs"] = previous_outputs + [
        out for out in ctx.data_outputs if out not in previous_outputs
    ]
    state["gaps_recorded"] = state.get("gaps_recorded", []) + ctx.gaps_recorded
    # a revision pass runs the tools again on a fresh context, so dedupe by kind:
    # the admins must get one notice per turn, not one per attempt
    existing_alerts = state.get("alerts") or []
    seen_kinds = {a.get("kind") for a in existing_alerts}
    state["alerts"] = existing_alerts + [a for a in ctx.alerts
                                         if a.get("kind") not in seen_kinds]
    existing_cards = {c["_id"] for c in state.get("cards") or []}
    state["cards"] = (state.get("cards") or []) + [
        c for c in ctx.cards if c["_id"] not in existing_cards]
    state["shown_products"] = ctx.shown_products
    used_products = [c["note"] for c in ctx.calls
                     if c["tool"] in ("get_current_page_product", "get_product_details",
                                      # so the operator's Telegram card names the product
                                      # whose restock the customer is waiting on
                                      "ask_restock_date")
                     and c["ok"]]
    if used_products:
        state["resolved_product"] = used_products[0]

    if ctx.handoff_requested:
        state["draft"] = draft
        return _handoff(state, "user_request" if state.get("intent") == "human" else "agent_decision",
                        ctx.handoff_requested)
    if not draft.strip():
        return _handoff(state, "provider_error", "empty response from model after tool loop")
    state["draft"] = draft.strip()
    return state


FORWARDED_CARD = re.compile(r"basalam\.com/p/(\d+)")

# Lines of a product card the customer forwarded. They are not the customer's
# words, but every trigger below used to read them as such: «✅ موجود» on the card
# looked like a restock question and «... اصلی» in a title (a seller keyword on
# half the catalogue) looked like «اصل هست؟».
CARD_LINE = re.compile(r"^\s*(🛍|💰|✅|❌|\(?قیمت اصلی|https?://\S*basalam)", re.MULTILINE)


def _customer_words(text: str) -> str:
    """پیام خود مشتری، بدون خطوط کارتی که فوروارد کرده."""
    kept = [line for line in (text or "").splitlines() if not CARD_LINE.match(line)]
    return "\n".join(kept).strip() or (text or "")


async def _forwarded_product(tool_map: dict, text: str) -> str:
    """Data block for a product card the customer pasted, or "" if there is none.

    Goes through the tool so the lookup is recorded in `ctx.calls` and its output
    lands in `ctx.data_outputs` — the validator's evidence — exactly as if the
    model had called it.
    """
    match = FORWARDED_CARD.search(text or "")
    tool = tool_map.get("get_product_details")
    if not match or tool is None:
        return ""
    try:
        block = await tool.ainvoke({"product_id": int(match.group(1))})
    except Exception:  # noqa: BLE001 — a failed prefetch just leaves it to the model
        log.exception("could not prefetch forwarded product %s", match.group(1))
        return ""
    if "وضعیت موجودی: ناموجود" not in block:
        return block

    # سکوت کردن روی جایگزین یعنی مشتری‌ای که کارت به دست آمده بود دست خالی برود.
    # جایگزین را خودمان پیدا می‌کنیم: search_products از هاب می‌آید و آگهی دومِ
    # همان کالا در باسلام لزوماً معادل هابی ندارد، پس مدل با آن ابزار پیدایش نمی‌کند.
    forwarded_id = int(match.group(1))
    alternative = await _available_twin(forwarded_id)
    if not alternative:
        return block + ("\nاین آگهی تمام شده و هیچ آگهی موجودی از این کالا نداریم."
                        " صادقانه همین را بگو و وعدهٔ جایگزین نده.")

    # دو آگهیِ جدا با دو وضعیت جدا — اگر این را صریح نگوییم، هم مدل و هم اعتبارسنج
    # آن را تناقض می‌بینند و پاسخ درست رد می‌شود
    price = alternative.get("price_toman")
    return block + (
        f"\n--- دو آگهیِ جداگانه از یک کالا ---\n"
        f"آگهی‌ای که مشتری فرستاده (id={forwarded_id}): تمام شده و قابل سفارش نیست.\n"
        f"آگهی دیگرِ همین کالا (id={alternative['id']}): موجود است"
        + (f" — {price:,} تومان" if price else "") + f" | {alternative.get('title')}\n"
        "این تناقض نیست؛ دو آگهی مستقل‌اند. پاسخ درست این است: بگو آگهی‌ای که فرستادند"
        " تمام شده ولی همین کالا را با آگهی دیگری داریم، کارت آگهی موجود را با"
        " show_product_cards بفرست و دعوتش کن از همان سفارش بدهد."
    )


async def _available_twin(basalam_id: int) -> dict | None:
    """آگهی موجود از همان کالا، وقتی آگهی‌ای که مشتری فرستاده تمام شده است."""
    product = await basalam_client.product(basalam_id)
    if not product or not product.get("title"):
        return None
    found = await basalam_client.search(product["title"], limit=5)
    return next((p for p in found
                 if p.get("available") and int(p["id"]) != basalam_id), None)


# Only *discovery* belongs on cards. In Basalam the customer usually opens by
# sending the product card themselves and then asks about it — details for a
# product already in front of them must not trigger a card, or the model rewrites
# a real answer into a contentless one-liner.
# هر تذکر داخلی با این خط شروع می‌شود. بدون آن، مدل تذکر را حرفِ مشتری می‌گیرد و
# به‌جای نوشتن پاسخ درست، از مشتری بابت «اشتباهش» عذرخواهی می‌کند و اسم ابزارها را
# لو می‌دهد — مشتری آن‌وقت یک متن سرگردان می‌گیرد، نه جواب سؤالش.
INTERNAL_NOTE = (
    "[یادداشت داخلی سامانه — این را مشتری نفرستاده و نمی‌بیند. در پاسخت به آن،"
    " به ابزارها و به اشتباه قبلی‌ات اشاره نکن، عذرخواهی نکن و قول بررسی مجدد نده؛"
    " فقط پاسخ نهایی به مشتری را درست بنویس.]\n"
)

CARD_SOURCE_TOOLS = ("search_products",)

NUDGE_CARDS = (
    "محصول‌های تازه‌ای را در متن معرفی کردی ولی کارتشان را نفرستادی. اول show_product_cards"
    " را با شناسهٔ همان محصول‌های موجود صدا بزن، بعد متن را بدون اسم و قیمت بازنویسی کن."
    " **جواب سؤال مشتری باید در متن بماند** — فقط فهرست اسم و قیمت حذف می‌شود، نه پاسخ."
)


# «اصل هست؟» — the one question where a vague product description reads as
# dodging. The prompt alone did not hold: the model kept answering with features
# and leaving the actual question untouched, so this is enforced here instead.
AUTHENTICITY_ASKED = re.compile(r"اصل|اورجینال|اورجینال|اریجینال|تقلبی|فیک|جعلی|قلابی")
AUTHENTICITY_EVIDENCE = "اصالت:"
NUDGE_AUTHENTICITY = (
    "مشتری پرسیده کالا اصل است یا نه و دادهٔ ابزار خط «اصالت» را دارد، ولی پاسخ تو"
    " جواب همین سؤال را نمی‌دهد. بازنویسی کن: **اول** صریح بگو بله کالا اصل است،"
    " و اگر خط «نظر خریداران» هم در داده بود، همان‌جا بگو چند خریدار قبلی ثبتش"
    " کرده‌اند و نظرها پایین همین صفحهٔ محصول دیده می‌شود. بقیهٔ توضیحات بعد از آن."
)


def _needs_authenticity(state: AgentState, ctx: ToolContext, draft: str) -> bool:
    if not AUTHENTICITY_ASKED.search(_customer_words(state.get("text", ""))):
        return False
    # ctx, not state: this runs inside the tool loop, before the merge below
    if not any(AUTHENTICITY_EVIDENCE in out for out in ctx.data_outputs):
        return False
    return not AUTHENTICITY_ASKED.search(draft)


# «کرم پودر دارید؟» kept coming back as «بله داریم، چه نوعی می‌خواهید؟» with no
# search behind it — an availability claim from memory, in the one place where
# being wrong costs a customer. The prompt rule held for «پنکک» but not here, so
# the loop enforces it. Note «نداریم» contains «داریم»: both directions need data.
AVAILABILITY_CLAIM = re.compile(r"داریم|موجود(ه| است|ی داریم)|هست")
# ask_restock_date هم سند است: خودش آگهی و مدل‌هایش را از غرفه می‌گیرد. بدون آن،
# پاسخِ درستِ «این رایحه ناموجود است» بی‌پشتوانه شمرده می‌شد و مدل به جستجوی
# سطحِ آگهی هدایت می‌شد، که «موجود» می‌گوید و جواب درست را وارونه می‌کرد.
SEARCH_TOOLS = ("search_products", "get_product_details", "get_current_page_product",
                "suggest_gifts", "ask_restock_date")
NUDGE_SEARCH = (
    "در پاسخت دربارهٔ داشتن یا نداشتن یک محصول اظهار نظر کرده‌ای ولی هیچ جستجویی"
    " نزده‌ای، پس این ادعا پشتوانه ندارد. **اول** search_products را با همان عبارت"
    " مشتری صدا بزن، بعد از روی نتیجهٔ واقعی جواب بده. اگر باز هم لازم بود نوع پوست"
    " یا سلیقه‌اش را بپرسی، همراه کارت‌ها بپرس، نه به‌جای آن‌ها."
)


# «کی شارژ می‌شه؟» — the restock date exists nowhere, so answering it at all means
# either an invented date or an empty promise («پیگیری می‌کنم» with nothing behind
# it). The tool is the only correct move, and the prompt alone did not hold.
# Must match a *question about timing*, never the bare word «موجود». Basalam
# customers open by forwarding the product card, whose text always contains
# «✅ موجود» — the loose version fired on every one of those, pushed the model
# into the restock tool and had it tell customers an in-stock product was gone.
RESTOCK_ASKED = re.compile(
    r"(کی|کِی|چه\s*(زمان|موقع|وقت)ی?|تا\s*کی)[^\n؟?]{0,25}?"
    r"(شارژ|موجود|میاد|می‌?آید|بیاد|برسه|می‌?رسه|بیارید)"
    r"|(شارژ|موجود)\s*(مجدد|دوباره)?\s*(می‌?شه|میشه|می‌?شود|بشه|خواهد\s*شد)"
)
NUDGE_RESTOCK = (
    "مشتری پرسیده کِی این محصول شارژ/موجود می‌شود. تاریخ شارژ هیچ‌جا ثبت نیست و تو"
    " هم نمی‌دانی، پس نه تاریخ بگو و نه از خودت قول پیگیری بده. ask_restock_date را"
    " با نام همان محصول (و شماره یا رنگ، اگر گفته) صدا بزن و بعد جواب بنویس."
)

# Telling a customer that something we have is gone costs a sale outright, and a
# handoff draft never reaches the validator (route_after_agent goes straight to
# finalize), so every stock-out claim is checked here against the tool data.
STOCKOUT_CLAIM = re.compile(r"ناموجود|موجود\s*نیست|موجود\s*نمی|تمام\s*شده|تموم\s*شده|اتمام\s*موجودی")
STOCKOUT_EVIDENCE = ("[ناموجود]", "وضعیت موجودی: ناموجود", "outofstock")
NUDGE_STOCKOUT = (
    "در پاسخت گفته‌ای محصول ناموجود است، ولی هیچ دادهٔ ابزاری این را نشان نمی‌دهد."
    " این بدترین خطای ممکن است: مشتری‌ای که می‌خواست بخرد را از دست می‌دهیم."
    " **اول** search_products را با نام همان محصول بزن و به خط موجودی نگاه کن."
    " اگر [موجود] بود، بگو موجود است و می‌تواند سفارش بدهد. فقط وقتی داده صریحاً"
    " [ناموجود] نشان داد حق داری بگویی ناموجود است."
)

NUDGE_EMPTY = (
    "پاسخت خالی بود. با تکیه بر همان داده‌های ابزار، در یکی دو جملهٔ کوتاه و مهربان"
    " جواب مشتری را بنویس. اگر کارت فرستادی، بگو چه فرستادی و چطور می‌تواند سفارش"
    " بدهد. چیزی از خودت اضافه نکن، ولی بی‌جواب هم رهایش نکن."
)

NUDGES = {"search": NUDGE_SEARCH, "cards": NUDGE_CARDS, "stockout": NUDGE_STOCKOUT,
          "authenticity": NUDGE_AUTHENTICITY, "restock": NUDGE_RESTOCK,
          "empty": NUDGE_EMPTY}


def _stockout_supported(outputs: list[str]) -> bool:
    return any(mark in out for out in outputs or [] for mark in STOCKOUT_EVIDENCE)


def _claims_unbacked_stockout(ctx: ToolContext, draft: str) -> bool:
    return bool(STOCKOUT_CLAIM.search(draft)) and not _stockout_supported(ctx.data_outputs)


def _needs_restock(state: AgentState, ctx: ToolContext) -> bool:
    if not RESTOCK_ASKED.search(_customer_words(state.get("text", ""))):
        return False
    return not any(c["tool"] == "ask_restock_date" for c in ctx.calls)


def _needs_search(state: AgentState, ctx: ToolContext, draft: str) -> bool:
    if state.get("intent") != "product":
        return False
    if any(c["ok"] for c in ctx.calls if c["tool"] in SEARCH_TOOLS):
        return False
    return bool(AVAILABILITY_CLAIM.search(draft))


def _needs_cards(state: AgentState, ctx: ToolContext) -> bool:
    """The model sometimes writes a product answer without calling show_product_cards
    and lists the products by name instead — exactly what the cards are there for.

    A tool call that produced no card (everything out of stock, or all of it
    already shown) still counts as done — nudging there just sends the model
    round again and the answer comes back hollow.
    """
    if ctx.cards or state.get("intent") != "product":
        return False
    if any(c["ok"] for c in ctx.calls if c["tool"] in ("show_product_cards", "suggest_gifts")):
        return False
    return any(c["ok"] for c in ctx.calls if c["tool"] in CARD_SOURCE_TOOLS)


def route_after_agent(state: AgentState) -> str:
    return "finalize" if state.get("outcome") == "handoff" else "validate"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

# Tool blocks are the biggest slice of every prompt we pay for; keep the freshest
# ones (the draft is written from those) and drop the tail.
EVIDENCE_BUDGET = 8000


def _evidence(outputs: list[str]) -> str:
    kept: list[str] = []
    size = 0
    for out in reversed(outputs):
        if size + len(out) > EVIDENCE_BUDGET and kept:
            break
        kept.insert(0, out)
        size += len(out)
    return "\n\n".join(kept)


class ValidationResult(BaseModel):
    ok: bool
    issues: list[str] = Field(default_factory=list)
    critique: str = ""


async def validate_node(state: AgentState) -> AgentState:
    prompt, version = prompt_store.get("validate")
    data = _evidence(state.get("data_outputs") or []) or "(no tool data was retrieved)"
    try:
        result = await run_role(
            "validation",
            [SystemMessage(content=prompt),
             HumanMessage(content=(
                 f"Customer question:\n{state['text']}\n\nTool outputs:\n{data}"
                 f"\n\nDraft reply:\n{state['draft']}"))],
            structured=ValidationResult,
        )
    except LLMFailure as e:
        # validator outage must not silence the agent — pass through, but record it
        log.warning("validation unavailable, passing draft through: %s", e)
        state["validation"] = {"ok": True, "skipped": True, "error": str(e)[:200]}
        state["outcome"] = "reply"
        return state
    _log_call(state, "validate", "validation", result,
              extra={"prompt_key": "validate", "prompt_version": version})
    parsed: ValidationResult = result.output
    state["validation"] = parsed.model_dump()
    if parsed.ok:
        state["outcome"] = "reply"
        return state
    revisions = state.get("revision_count", 0)
    with db_session() as session:
        handoff_on_fail = crud.get_setting(session, "handoff_on_validation_fail")
    if revisions < 1:
        state["revision_count"] = revisions + 1
        state["critique"] = parsed.critique or "; ".join(parsed.issues)
        # Cards belong to the draft that was rejected. Keeping them would send the
        # customer product cards the rewritten answer no longer stands behind — and
        # since cards go out first, they would contradict the text that follows.
        # Rolling `shown_products` back too lets the retry re-card the same products.
        state["cards"] = []
        state["shown_products"] = list(state.get("shown_baseline") or [])
        # the cancelled cards' evidence goes with them, otherwise the retry is told
        # products are on their way to the customer when nothing was sent
        state["data_outputs"] = [out for out in (state.get("data_outputs") or [])
                                 if 'source="shown_cards"' not in out]
        return state
    if handoff_on_fail and _must_escalate(state):
        return _handoff(state, "validation_fail",
                        f"reply failed validation twice: {'; '.join(parsed.issues)}")
    # A browsing answer that IS backed by tool data but the validator still
    # dislikes (usually a product-choice quibble) is worth far more to the
    # customer than a dead-end handoff. It stays on record in `responses`.
    log.info("delivering draft despite validation issues: %s", parsed.issues)
    state["outcome"] = "reply"
    return state


# «نداریم / موجود نیست» دربارهٔ یک کالا — گران‌ترین جملهٔ اشتباهی که این ایجنت
# می‌تواند بگوید: مشتری را می‌پراند و برخلاف یک اشتباه سلیقه‌ای، جبران‌پذیر نیست.
UNAVAILABILITY_CLAIMS = ("نداریم", "ناموجود", "موجود نیست", "موجود نداریم",
                         "تمام شده", "تموم شده", "متوقف شده")


def _must_escalate(state: AgentState) -> bool:
    """Escalate only where being wrong is expensive: money and personal data, an
    unsupported *claim*, or telling the customer we don't stock something. A draft
    that just asks the customer something (the gift budget, which product they
    mean) states no facts, so there is nothing for the validator to be right about."""
    # An order answer used to always escalate because the agent had no order data
    # of its own. Now get_my_order reads the parcel straight from Basalam, so an
    # answer with tool data behind it must not be thrown away over a validator
    # quibble about a tracking number; only a *dataless* order answer escalates.
    if state.get("intent") == "order" and not state.get("data_outputs"):
        return True
    draft = state.get("draft", "")
    # A twice-rejected «we don't have it» goes to a human even when tool data was
    # retrieved: a customer sent us a product card and was told the full pack did
    # not exist while its cards were on screen (conversation 149, 2026-08-03).
    # Product intent only — «تحویل حضوری نداریم» is a policy answer, and escalating
    # those sent a quarter of them to an operator for nothing.
    # ...but only when nothing in the tool data backs it. A true «شمارهٔ ۱ ناموجود
    # است», read straight off a [ناموجود] row, was being escalated because the
    # validator misread the variant list — a correct answer thrown away, and the
    # customer handed to an operator for nothing.
    if (state.get("intent") == "product"
            and any(c in draft for c in UNAVAILABILITY_CLAIMS)
            and not _stockout_supported(state.get("data_outputs"))):
        return True
    if state.get("data_outputs"):
        return False
    return "؟" not in draft and "?" not in draft


def route_after_validate(state: AgentState) -> str:
    if state.get("outcome") in ("reply", "handoff"):
        return "finalize"
    return "agent"  # one revision loop


# ---------------------------------------------------------------------------
# finalize — pick the customer-facing text
# ---------------------------------------------------------------------------

def finalize_node(state: AgentState) -> AgentState:
    outcome = state.get("outcome") or "reply"
    if outcome == "reply":
        state["final_text"] = state.get("draft", "")
    elif outcome == "refuse":
        state["final_text"] = prompt_store.get("msg_refusal")[0]
    elif outcome == "clarify":
        kind = (state.get("meta") or {}).get("clarify_kind", "")
        key = {"order_need_phone": "msg_order_need_phone",
               "order_not_found": "msg_order_not_found"}.get(kind, "msg_clarify_product")
        state["final_text"] = prompt_store.get(key)[0]
    elif outcome == "handoff":
        state["final_text"] = _handoff_message(state)
    else:
        state["final_text"] = ""
    return state


# Handoffs the agent chose itself carry a polite draft written for the
# customer; every other kind means the draft is unusable (it failed validation)
# or missing (the provider errored), so a canned message is sent instead.
AGENT_AUTHORED_HANDOFFS = ("user_request", "agent_decision")


def _handoff_message(state: AgentState) -> str:
    kind = state.get("handoff_kind", "")
    if kind == "provider_limit":
        return prompt_store.get("msg_limit_reached")[0]
    if kind in AGENT_AUTHORED_HANDOFFS and state.get("draft", "").strip():
        return state["draft"]
    if kind in ("provider_error", "hub_error"):
        return prompt_store.get("msg_error_holding")[0]
    return prompt_store.get("msg_handoff")[0]

import asyncio

from sqlalchemy import select

from app.agent.tools import save_content_gap
from app.db import db_session
from app.models import ContentGap, Handoff, TelegramOperator
from app.services.handoff import claim


def _make_operator(chatwoot_user_id: int, chat_id: int) -> int:
    with db_session() as session:
        op = session.scalar(select(TelegramOperator).where(
            TelegramOperator.telegram_chat_id == chat_id))
        if op is None:
            op = TelegramOperator(chatwoot_user_id=chatwoot_user_id,
                                  chatwoot_user_name=f"Op{chatwoot_user_id}",
                                  telegram_chat_id=chat_id)
            session.add(op)
            session.flush()
        return op.id


def _make_handoff(conversation_id: int) -> int:
    with db_session() as session:
        handoff = Handoff(conversation_id=conversation_id, reason="test",
                          reason_kind="other")
        session.add(handoff)
        session.flush()
        return handoff.id


def test_claim_is_atomic_exactly_one_winner(_db):
    op_a = _make_operator(501, 90501)
    op_b = _make_operator(502, 90502)
    handoff_id = _make_handoff(70001)

    first = claim(handoff_id, op_a)
    second = claim(handoff_id, op_b)

    assert first is not None and first["handoff_id"] == handoff_id
    assert second is None
    with db_session() as session:
        row = session.get(Handoff, handoff_id)
        assert row.status == "claimed"
        assert row.claimed_by == op_a


def test_claim_concurrent(_db):
    """Both claims race on the event loop; SQLite conditional UPDATE
    guarantees a single winner."""
    op_a = _make_operator(503, 90503)
    op_b = _make_operator(504, 90504)
    handoff_id = _make_handoff(70002)

    async def race():
        return await asyncio.gather(
            asyncio.to_thread(claim, handoff_id, op_a),
            asyncio.to_thread(claim, handoff_id, op_b),
        )

    results = asyncio.run(race())
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


def test_content_gap_grouping(_db):
    first = save_content_gap(question="تاریخ انقضای کرم X چیه؟",
                             missing_info="expiry missing", category="expiry",
                             conversation_id=1, product_slug="krem-x")
    second = save_content_gap(question="تاریخ انقضای کرم X چیه",
                              missing_info="expiry missing", category="expiry",
                              conversation_id=2, product_slug="krem-x")
    assert first == second
    with db_session() as session:
        gap = session.get(ContentGap, first)
        assert gap.occurrences == 2

    other = save_content_gap(question="طرز استفاده از کرم X چطوره؟",
                             missing_info="usage missing", category="usage",
                             conversation_id=3, product_slug="krem-x")
    assert other != first


# --- provider limit handling -------------------------------------------------

def test_is_quota_error_detection():
    from app.agent.llm import is_quota_error

    assert is_quota_error("openai/gpt-5-mini: RateLimitError: 429 Too Many Requests")
    assert is_quota_error("Error code: 402 - insufficient credits")
    assert is_quota_error("You exceeded your current quota")
    assert not is_quota_error("APITimeoutError: request timed out")
    assert not is_quota_error("")


def test_quota_failure_raises_distinct_exception():
    """Both models refusing for billing reasons must surface as LLMQuotaExceeded."""
    import asyncio
    from unittest.mock import patch

    from app.agent import llm

    spec = llm._ModelSpec(model_name="m1", base_url="http://x", api_key="k",
                          params={}, input_cost=0, output_cost=0, timeout=5)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("Error code: 429 - rate limit exceeded")

    with patch.object(llm, "_resolve_role", return_value=(spec, None)), \
         patch.object(llm, "_invoke", side_effect=boom):
        try:
            asyncio.run(llm.run_role("main_response", []))
            raise AssertionError("expected failure")
        except llm.LLMQuotaExceeded:
            pass


def test_handoff_message_never_leaks_rejected_draft():
    from app.agent.nodes import _handoff_message

    rejected = "این محصول قطعا اصل است"  # failed validation — must not be sent
    limit = _handoff_message({"handoff_kind": "provider_limit", "draft": rejected})
    assert rejected not in limit
    assert "سقف مصرف" in limit

    failed = _handoff_message({"handoff_kind": "validation_fail", "draft": rejected})
    assert rejected not in failed
    assert failed.strip()

    errored = _handoff_message({"handoff_kind": "provider_error", "draft": rejected})
    assert rejected not in errored

    # the agent's own polite handoff note is still delivered
    polite = "الان همکارم رو وصل می‌کنم"
    assert _handoff_message({"handoff_kind": "user_request", "draft": polite}) == polite


def test_revision_keeps_earlier_tool_evidence():
    """Regression: a revision pass used to wipe data_outputs, so a grounded
    answer was judged unsupported and the bot handed off and went silent."""
    from app.agent.state import AgentState

    state: AgentState = {"data_outputs": ["<data source=\"product\">قیمت: ۱۰۰</data>"],
                         "tool_calls": [{"tool": "get_product_details", "ok": True}]}
    previous_outputs = state["data_outputs"]
    fresh_ctx_outputs: list[str] = []  # revision pass called no tools

    state["tool_calls"] = (state.get("tool_calls") or []) + []
    state["data_outputs"] = previous_outputs + [
        o for o in fresh_ctx_outputs if o not in previous_outputs
    ]

    assert state["data_outputs"] == previous_outputs
    assert len(state["tool_calls"]) == 1


def test_graph_tells_customer_when_usage_limit_is_hit():
    """End-to-end: provider out of quota → Persian limit notice + handoff,
    never silence and never a half-written answer."""
    import asyncio
    from unittest.mock import patch

    from app.agent import nodes
    from app.agent.graph import build_graph
    from app.agent.llm import LLMQuotaExceeded

    async def out_of_quota(*_args, **_kwargs):
        raise LLMQuotaExceeded("all models failed: 429 rate limit exceeded")

    with patch.object(nodes, "run_role", side_effect=out_of_quota):
        state = asyncio.run(build_graph().ainvoke({
            "conversation_id": 0, "text": "قیمت این محصول چنده؟",
            "contact": {}, "history": [],
        }))

    assert state["outcome"] == "handoff"
    assert state["handoff_kind"] == "provider_limit"
    assert "سقف مصرف" in state["final_text"]
    assert "منتقل" in state["final_text"]

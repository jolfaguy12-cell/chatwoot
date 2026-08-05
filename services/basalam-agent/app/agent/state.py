"""LangGraph agent state."""

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # input
    conversation_id: int
    text: str                    # merged customer message(s), incl. voice transcripts
    contact: dict                # {id, name, phone_norm, email}
    page_url: str
    product_slug: str
    history: list[dict]          # [{role: 'user'|'assistant', content}]
    cart: dict                   # WooCommerce cart snapshot pushed by the site
    shown_products: list[int]    # already carded in this conversation
    reply_context: str           # the bubble this message replies to (card titles / text)
    fragmented: bool             # this turn arrived as several separate bubbles
    greeting_allowed: bool       # nothing has gone out to this customer today (Tehran)
    test_mode: bool              # sandbox runs never touch Chatwoot

    # classification
    intent: str                  # product|order|policy|greeting|human|other
    in_scope: bool
    product_mention: str
    product_url: str

    # generation
    draft: str
    critique: str
    revision_count: int
    tool_calls: list[dict]
    data_outputs: list[str]
    gaps_recorded: list[int]
    cards: list[dict]            # {_id, _ids, basalam_id, title} — sent once validated
    shown_baseline: list[int]    # shown_products before this run — restored on a revision
    alerts: list[dict]           # admin Telegram notices that do NOT take the conversation

    # outcome
    outcome: str                 # reply | refuse | clarify | handoff | silent
    handoff_reason: str
    handoff_kind: str
    validation: dict
    run_logs: list[dict]         # per-LLM-call telemetry rows
    final_text: str
    resolved_product: str
    meta: dict[str, Any]

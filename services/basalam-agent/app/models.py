"""Service-owned state. All AI configuration and telemetry lives here —
nothing is hardcoded, nothing is stored in Chatwoot's DB."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(255))
    api_key_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet; empty = use env key
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Model(Base):
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"))
    model_name: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    input_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)   # USD
    output_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)  # USD
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # temperature, max_tokens...
    __table_args__ = (UniqueConstraint("provider_id", "model_name"),)


ROLES = (
    "intent_routing", "main_response", "fallback_response", "extraction",
    "validation", "evaluation", "stt", "embeddings",
)


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    primary_model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"), nullable=True)
    fallback_model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"), nullable=True)
    traffic_split: Mapped[list] = mapped_column(JSON, default=list)  # [{model_id, pct}]
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # timeout_seconds etc.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Prompt(Base):
    __tablename__ = "prompts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active_version: Mapped[int] = mapped_column(Integer, default=1)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_key: Mapped[str] = mapped_column(ForeignKey("prompts.key"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("prompt_key", "version"),)


# --------------------------------------------------------------------------
# Conversation control state
# --------------------------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"
    display_id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="ai")  # ai|human|disabled
    # Chatwoot inbox the visitor chats through — selects the data hub
    # (dev site -> dev hub, main site -> main hub). See config.hub_for_inbox.
    inbox_id: Mapped[int] = mapped_column(Integer, default=0)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    contact_phone_norm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_url: Mapped[str] = mapped_column(Text, default="")
    product_slug: Mapped[str] = mapped_column(String(512), default="")
    # WooCommerce cart snapshot pushed by the site — only the shopper's own
    # browser can read their cart session, so this is the agent's only view of it
    cart: Mapped[dict] = mapped_column(JSON, default=dict)
    # product ids already carded in this conversation — never show one twice
    shown_products: Mapped[list] = mapped_column(JSON, default=list)
    # phone/email the customer supplied in-chat and we verified against Hub orders
    verified_identity: Mapped[dict] = mapped_column(JSON, default=dict)
    assignee_operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_auth_failures: Mapped[int] = mapped_column(Integer, default=0)
    # turns where the customer split one request across several bubbles
    fragmented_turns: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    event_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MessageJob(Base):
    __tablename__ = "message_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(Integer, index=True)
    message_ids: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | running | done | failed | abandoned
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------

class ResponseLog(Base):
    __tablename__ = "responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="reply", index=True)
    # reply | clarify | refuse | handoff | stt | classify | validate | test | eval
    role: Mapped[str] = mapped_column(String(32), default="")
    model_used: Mapped[str] = mapped_column(String(128), default="", index=True)
    prompt_key: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)  # ok|error|fallback
    error: Mapped[str] = mapped_column(Text, default="")
    input_text: Mapped[str] = mapped_column(Text, default="")
    output_text: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    intent: Mapped[str] = mapped_column(String(32), default="")
    product_slug: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("responses.id"), index=True)
    rater_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rater_name: Mapped[str] = mapped_column(String(128), default="")
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)  # {dimension: 1..5}
    comment: Mapped[str] = mapped_column(Text, default="")
    corrected_response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TestCase(Base):
    __tablename__ = "test_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    tags: Mapped[str] = mapped_column(String(255), default="")
    input: Mapped[dict] = mapped_column(JSON, default=dict)  # {text, page_url, history...}
    expectation_notes: Mapped[str] = mapped_column(Text, default="")
    preferred_answer: Mapped[str] = mapped_column(Text, default="")
    source_response_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TestRun(Base):
    __tablename__ = "test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id"), index=True)
    batch_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    config_note: Mapped[str] = mapped_column(String(255), default="")
    output_text: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(24), default="")
    model_used: Mapped[str] = mapped_column(String(128), default="")
    auto_eval: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContentGap(Base):
    __tablename__ = "content_gaps"
    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    question_norm: Mapped[str] = mapped_column(Text, default="", index=True)
    category: Mapped[str] = mapped_column(String(64), default="")  # expiry|usage|specs|policy|other
    product_slug: Mapped[str] = mapped_column(String(512), default="")
    product_name: Mapped[str] = mapped_column(String(512), default="")
    page_url: Mapped[str] = mapped_column(Text, default="")
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_info: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # open | in_progress | resolved | wont_fix
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0 none, 1 low, 2 med, 3 high
    owner: Mapped[str] = mapped_column(String(128), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    example_questions: Mapped[list] = mapped_column(JSON, default=list)
    last_retest: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------
# Handoff / Telegram
# --------------------------------------------------------------------------

class Handoff(Base):
    __tablename__ = "handoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(Integer, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    reason_kind: Mapped[str] = mapped_column(String(32), default="other")
    # user_request | missing_info | validation_fail | provider_error | hub_error
    # | order_auth_fail | sensitive | other
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | claimed | resolved | returned
    claimed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # telegram_operators.id
    claimed_via: Mapped[str] = mapped_column(String(16), default="")  # telegram|dashboard
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TelegramOperator(Base):
    __tablename__ = "telegram_operators"
    id: Mapped[int] = mapped_column(primary_key=True)
    chatwoot_user_id: Mapped[int] = mapped_column(Integer, unique=True)
    chatwoot_user_name: Mapped[str] = mapped_column(String(255), default="")
    telegram_chat_id: Mapped[int] = mapped_column(Integer, unique=True)
    telegram_username: Mapped[str] = mapped_column(String(128), default="")
    pref: Mapped[str] = mapped_column(String(16), default="both")
    # dashboard | telegram | both | disabled
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LinkCode(Base):
    __tablename__ = "link_codes"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    chatwoot_user_id: Mapped[int] = mapped_column(Integer)
    chatwoot_user_name: Mapped[str] = mapped_column(String(255), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TelegramMsgMap(Base):
    __tablename__ = "telegram_msg_map"
    telegram_chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(Integer, index=True)
    handoff_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="notify")  # notify|console|relay
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------
# Audit / cache
# --------------------------------------------------------------------------

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    via: Mapped[str] = mapped_column(String(16), default="system")  # dashboard|telegram|system
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class HubCache(Base):
    __tablename__ = "hub_cache"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

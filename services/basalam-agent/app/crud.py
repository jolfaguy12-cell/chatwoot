"""Settings/config accessors and small shared helpers. Hot-loaded per use —
changing a setting or model in the dashboard takes effect on the next run."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Conversation,
    Model,
    Prompt,
    PromptVersion,
    Provider,
    RoleAssignment,
    Setting,
)

DEFAULT_SETTINGS: dict[str, dict] = {
    "ai_enabled": {"value": True},
    "debounce_seconds": {"value": 4},
    "history_limit": {"value": 12},
    "max_tool_iterations": {"value": 4},
    "main_timeout_seconds": {"value": 60},
    "aux_timeout_seconds": {"value": 25},
    "order_auth_max_failures": {"value": 3},
    "handoff_on_validation_fail": {"value": True},
    "langsmith_enabled": {"value": False},
}


def get_setting(session: Session, key: str):
    row = session.get(Setting, key)
    if row is not None:
        return row.value.get("value")
    return DEFAULT_SETTINGS.get(key, {}).get("value")


def set_setting(session: Session, key: str, value) -> None:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value={"value": value})
        session.add(row)
    else:
        row.value = {"value": value}


def all_settings(session: Session) -> dict:
    out = {k: v["value"] for k, v in DEFAULT_SETTINGS.items()}
    for row in session.scalars(select(Setting)):
        out[row.key] = row.value.get("value")
    return out


def get_conversation(session: Session, display_id: int) -> Conversation:
    conv = session.get(Conversation, display_id)
    if conv is None:
        conv = Conversation(display_id=display_id)
        session.add(conv)
        session.flush()
    return conv


def get_role(session: Session, role: str) -> RoleAssignment | None:
    return session.get(RoleAssignment, role)


def get_model(session: Session, model_id: int | None) -> Model | None:
    return session.get(Model, model_id) if model_id else None


def get_provider(session: Session, provider_id: int) -> Provider | None:
    return session.get(Provider, provider_id)


def active_prompt(session: Session, key: str) -> tuple[str, int]:
    """Return (content, version) of the active version of a prompt."""
    prompt = session.get(Prompt, key)
    if prompt is None:
        return "", 0
    pv = session.scalar(
        select(PromptVersion).where(
            PromptVersion.prompt_key == key, PromptVersion.version == prompt.active_version
        )
    )
    return (pv.content, pv.version) if pv else ("", 0)


def add_prompt_version(session: Session, key: str, content: str, notes: str = "",
                       created_by: str = "system", activate: bool = True) -> PromptVersion:
    prompt = session.get(Prompt, key)
    if prompt is None:
        prompt = Prompt(key=key, active_version=0)
        session.add(prompt)
        session.flush()
    latest = session.scalar(
        select(PromptVersion.version).where(PromptVersion.prompt_key == key)
        .order_by(PromptVersion.version.desc()).limit(1)
    ) or 0
    pv = PromptVersion(prompt_key=key, version=latest + 1, content=content,
                       notes=notes, created_by=created_by)
    session.add(pv)
    if activate:
        prompt.active_version = pv.version
    return pv


def audit(session: Session, action: str, *, actor: str = "system", via: str = "system",
          entity: str = "", entity_id: str = "", before: dict | None = None,
          after: dict | None = None) -> None:
    session.add(AuditLog(actor=actor, via=via, action=action, entity=entity,
                         entity_id=str(entity_id), before=before or {}, after=after or {}))


def prune_expired(session: Session) -> None:
    """Housekeeping for dedup keys and cache rows (called opportunistically)."""
    from app.models import HubCache, ProcessedEvent

    now = datetime.now(timezone.utc)
    session.query(ProcessedEvent).filter(
        ProcessedEvent.received_at < now - timedelta(days=7)
    ).delete()
    session.query(HubCache).filter(HubCache.expires_at < now - timedelta(days=1)).delete()

"""Active-prompt loading (DB-backed, hot). Falls back to code defaults only if
the DB has never been seeded."""

from app import crud
from app.agent import default_prompts
from app.db import db_session


def get(key: str) -> tuple[str, int]:
    """Return (content, version) for the active version of a prompt key."""
    with db_session() as session:
        content, version = crud.active_prompt(session, key)
    if content:
        return content, version
    default = default_prompts.DEFAULTS.get(key)
    return (default[1], 0) if default else ("", 0)


def seed_defaults() -> int:
    """Insert any missing prompts as version 1. Never overwrites existing ones."""
    created = 0
    with db_session() as session:
        for key, (description, content) in default_prompts.DEFAULTS.items():
            existing, _ = crud.active_prompt(session, key)
            if not existing:
                pv = crud.add_prompt_version(session, key, content, notes="seed default")
                prompt = session.get(crud.Prompt, key)
                prompt.description = description
                created += 1
                assert pv.version >= 1
    return created

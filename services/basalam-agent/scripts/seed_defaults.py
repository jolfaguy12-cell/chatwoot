"""Seed the default provider (OpenRouter), models and role assignments.
Idempotent — never overwrites existing rows. Prompts are seeded on service
startup (prompt_store.seed_defaults); this script covers models/roles.

Run:  .venv/bin/python -m scripts.seed_defaults
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import db_session, init_db  # noqa: E402
from app.models import Model, Provider, RoleAssignment  # noqa: E402

# (model_name, label, supports_audio, input_cost, output_cost $/Mtok)
DEFAULT_MODELS = [
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash", True, 0.30, 2.50),
    ("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", True, 0.10, 0.40),
    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash", True, 0.10, 0.40),
    ("openai/gpt-5-mini", "GPT-5 Mini", False, 0.25, 2.00),
    ("openai/gpt-5-nano", "GPT-5 Nano", False, 0.05, 0.40),
]

# role -> (primary model_name, fallback model_name)
DEFAULT_ROLES = {
    "intent_routing": ("google/gemini-2.5-flash-lite", "openai/gpt-5-nano"),
    "main_response": ("google/gemini-2.5-flash", "openai/gpt-5-mini"),
    "fallback_response": ("openai/gpt-5-mini", "google/gemini-2.5-flash-lite"),
    "extraction": ("google/gemini-2.5-flash-lite", "openai/gpt-5-nano"),
    "validation": ("google/gemini-2.5-flash-lite", "openai/gpt-5-mini"),
    "evaluation": ("google/gemini-2.5-flash", "openai/gpt-5-mini"),
    "stt": ("google/gemini-2.5-flash", "google/gemini-2.0-flash-001"),
}


def seed() -> None:
    init_db()
    with db_session() as session:
        provider = session.scalar(select(Provider).where(Provider.name == "OpenRouter"))
        if provider is None:
            provider = Provider(name="OpenRouter", kind="openai_compatible",
                                base_url="https://openrouter.ai/api/v1")
            session.add(provider)
            session.flush()
            print("created provider OpenRouter (api key from env)")

        models: dict[str, Model] = {}
        for name, label, audio, in_cost, out_cost in DEFAULT_MODELS:
            model = session.scalar(select(Model).where(
                Model.provider_id == provider.id, Model.model_name == name))
            if model is None:
                model = Model(provider_id=provider.id, model_name=name, label=label,
                              supports_audio=audio, input_cost_per_mtok=in_cost,
                              output_cost_per_mtok=out_cost)
                session.add(model)
                session.flush()
                print(f"created model {name}")
            models[name] = model

        for role, (primary, fallback) in DEFAULT_ROLES.items():
            if session.get(RoleAssignment, role) is None:
                session.add(RoleAssignment(
                    role=role,
                    primary_model_id=models[primary].id,
                    fallback_model_id=models[fallback].id,
                ))
                print(f"assigned role {role}: {primary} (fallback {fallback})")
        if session.get(RoleAssignment, "embeddings") is None:
            session.add(RoleAssignment(role="embeddings", enabled=False))
            print("role embeddings: disabled (no OpenRouter embeddings endpoint)")


if __name__ == "__main__":
    seed()
    print("seed complete")

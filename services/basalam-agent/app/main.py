"""Behdashtik AI support service — FastAPI app factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import crud
from app.agent import prompt_store, runner
from app.api import chatwoot_webhook, hub_webhook, telegram_webhook
from app.api.admin import router as admin_router
from app.config import get_settings
from app.db import db_session, init_db
from app.services import telegram_bot
from app.workers import queue

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs, which include the Telegram bot token —
    # never let credentials reach the journal.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    init_db()
    created = prompt_store.seed_defaults()
    if created:
        log.info("seeded %d default prompts", created)
    with db_session() as session:
        crud.prune_expired(session)

    queue.set_handlers(runner.handle_messages, runner.handle_failure)
    stale = queue.recover_on_startup()
    for conversation_id in stale:
        try:
            await runner.handle_failure(conversation_id, "service was down during processing")
        except Exception:  # noqa: BLE001
            log.exception("stale-job handoff failed for conversation %s", conversation_id)

    try:
        await telegram_bot.start_bot()
    except Exception:  # noqa: BLE001 — the web service must come up even if Telegram is down
        log.exception("telegram bot failed to start")

    yield

    await telegram_bot.stop_bot()
    from app.services import chatwoot_client, hub_client

    await chatwoot_client.aclose()
    await hub_client.aclose()


app = FastAPI(title="Behdashtik AI Support Service", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(chatwoot_webhook.router)
app.include_router(hub_webhook.router)
app.include_router(telegram_webhook.router)
app.include_router(admin_router.router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, workers=1)

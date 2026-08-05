"""Durable per-conversation message queue.

Every accepted webhook message becomes a MessageJob row *before* the webhook
is acked, so nothing is lost across restarts. A single asyncio task per
conversation drains its jobs sequentially: debounce-merge rapid messages,
run the handler (the LangGraph agent), retry with backoff, and hand off to a
human when processing ultimately fails. Startup re-queues unfinished jobs.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import select

from app.db import db_session
from app.models import MessageJob
from app import crud

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [5, 20]
RECOVERY_WINDOW = timedelta(minutes=30)

# handler(conversation_id, merged_payloads) -> None; raises to trigger retry
Handler = Callable[[int, list[dict]], Awaitable[None]]
_handler: Handler | None = None
# failure_handler(conversation_id, error) -> None; called after final failure
FailureHandler = Callable[[int, str], Awaitable[None]]
_failure_handler: FailureHandler | None = None

_tasks: dict[int, asyncio.Task] = {}
_wakeups: dict[int, asyncio.Event] = {}


def set_handlers(handler: Handler, failure_handler: FailureHandler) -> None:
    global _handler, _failure_handler
    _handler = handler
    _failure_handler = failure_handler


def enqueue(conversation_id: int, message_id: int | None, payload: dict) -> int:
    with db_session() as session:
        job = MessageJob(
            conversation_id=conversation_id,
            message_ids=[message_id] if message_id else [],
            payload=payload,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    kick(conversation_id)
    return job_id


def kick(conversation_id: int) -> None:
    """Ensure a drain task exists for this conversation and wake it."""
    event = _wakeups.setdefault(conversation_id, asyncio.Event())
    event.set()
    task = _tasks.get(conversation_id)
    if task is None or task.done():
        _tasks[conversation_id] = asyncio.get_event_loop().create_task(
            _drain(conversation_id), name=f"drain-{conversation_id}"
        )


async def _drain(conversation_id: int) -> None:
    event = _wakeups[conversation_id]
    try:
        while True:
            event.clear()
            with db_session() as session:
                debounce = crud.get_setting(session, "debounce_seconds") or 0
            if debounce:
                await asyncio.sleep(float(debounce))

            jobs = _claim_queued(conversation_id)
            if not jobs:
                # nothing left; wait briefly for a wakeup then exit the task
                try:
                    await asyncio.wait_for(event.wait(), timeout=1.0)
                    continue
                except asyncio.TimeoutError:
                    return
            await _run_jobs(conversation_id, jobs)
    finally:
        _tasks.pop(conversation_id, None)


def _claim_queued(conversation_id: int) -> list[dict]:
    with db_session() as session:
        rows = list(session.scalars(
            select(MessageJob).where(
                MessageJob.conversation_id == conversation_id,
                MessageJob.status == "queued",
            ).order_by(MessageJob.id)
        ))
        for row in rows:
            row.status = "running"
            row.attempts += 1
        return [
            {"id": r.id, "message_ids": r.message_ids, "payload": r.payload,
             "attempts": r.attempts}
            for r in rows
        ]


def _mark(job_ids: list[int], status: str, error: str = "") -> None:
    with db_session() as session:
        for row in session.scalars(select(MessageJob).where(MessageJob.id.in_(job_ids))):
            row.status = status
            row.error = error[:2000]


async def _run_jobs(conversation_id: int, jobs: list[dict]) -> None:
    job_ids = [j["id"] for j in jobs]
    payloads = [j["payload"] for j in jobs]
    attempts = max(j["attempts"] for j in jobs)
    try:
        assert _handler is not None, "queue handler not wired"
        await _handler(conversation_id, payloads)
        _mark(job_ids, "done")
    except Exception as e:  # noqa: BLE001 — every failure must be visible + recoverable
        log.exception("job run failed for conversation %s", conversation_id)
        if attempts < MAX_ATTEMPTS:
            _mark(job_ids, "queued", error=str(e))
            backoff = RETRY_BACKOFF_SECONDS[min(attempts, len(RETRY_BACKOFF_SECONDS)) - 1]
            await asyncio.sleep(backoff)
            kick(conversation_id)
        else:
            _mark(job_ids, "failed", error=str(e))
            if _failure_handler is not None:
                try:
                    await _failure_handler(conversation_id, str(e))
                except Exception:  # noqa: BLE001
                    log.exception("failure handler failed for conversation %s", conversation_id)


def recover_on_startup() -> list[int]:
    """Re-queue unfinished jobs from before a restart; abandon stale ones.

    Returns conversation ids that had stale (abandoned) jobs so the caller can
    hand them to a human — a customer was left waiting through the outage.
    """
    stale_convs: set[int] = set()
    fresh_convs: set[int] = set()
    cutoff = datetime.now(timezone.utc) - RECOVERY_WINDOW
    with db_session() as session:
        rows = list(session.scalars(
            select(MessageJob).where(MessageJob.status.in_(["queued", "running"]))
        ))
        for row in rows:
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                row.status = "abandoned"
                stale_convs.add(row.conversation_id)
            else:
                row.status = "queued"
                fresh_convs.add(row.conversation_id)
    for conv_id in fresh_convs:
        kick(conv_id)
    return sorted(stale_convs)

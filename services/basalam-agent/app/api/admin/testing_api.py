"""Admin API: sandboxed test chat, test cases, batch test runs with auto-eval,
content-gap retesting."""

import logging
import secrets

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app import crud
from app.agent import prompt_store
from app.agent.graph import get_graph
from app.agent.llm import LLMFailure, run_role
from app.db import db_session
from app.models import ContentGap, ResponseLog, TestCase, TestRun

log = logging.getLogger(__name__)
router = APIRouter()

TEST_CONVERSATION_ID = 0  # sandbox — never touches Chatwoot


class TestChatIn(BaseModel):
    text: str
    page_url: str = ""
    product_slug: str = ""
    history: list[dict] = []
    inbox_id: int = 0  # which store to answer from; defaults to the main site


async def _run_sandbox(body: TestChatIn) -> dict:
    from app.config import get_settings
    from app.services import hub_client

    hub_client.use_inbox(body.inbox_id or get_settings().main_inbox_id)
    state = await get_graph().ainvoke({
        "conversation_id": TEST_CONVERSATION_ID,
        "text": body.text,
        "contact": {},
        "page_url": body.page_url,
        "product_slug": body.product_slug,
        "history": body.history,
        # a supplied history means we have already spoken today, which is what
        # the greeting rule keys off — without it the sandbox always greets
        "greeting_allowed": not body.history,
        "test_mode": True,
    })
    run_logs = state.get("run_logs") or []
    return {
        "outcome": state.get("outcome"),
        "final_text": state.get("final_text"),
        "intent": state.get("intent"),
        "handoff_kind": state.get("handoff_kind", ""),
        "handoff_reason": state.get("handoff_reason", ""),
        "tool_calls": state.get("tool_calls") or [],
        "validation": state.get("validation") or {},
        "cost_usd": sum(c.get("cost_usd", 0) for c in run_logs),
        "latency_ms": sum(c.get("latency_ms", 0) for c in run_logs),
        "models": sorted({c.get("model_used") for c in run_logs if c.get("model_used")}),
    }


@router.post("/testchat")
async def testchat(body: TestChatIn):
    """Run the full agent graph in a sandbox (no Chatwoot side effects)."""
    result = await _run_sandbox(body)
    return result


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------

def _case_out(c: TestCase) -> dict:
    return {"id": c.id, "name": c.name, "tags": c.tags, "input": c.input,
            "expectation_notes": c.expectation_notes,
            "preferred_answer": c.preferred_answer,
            "source_response_id": c.source_response_id,
            "created_at": c.created_at.isoformat()}


class TestCaseIn(BaseModel):
    name: str
    tags: str = ""
    input: dict
    expectation_notes: str = ""
    preferred_answer: str = ""


@router.get("/test_cases")
async def list_cases():
    with db_session() as session:
        return [_case_out(c) for c in
                session.scalars(select(TestCase).order_by(desc(TestCase.id)))]


@router.post("/test_cases")
async def create_case(body: TestCaseIn, actor: str = ""):
    if not body.input.get("text"):
        raise HTTPException(400, "input.text is required")
    with db_session() as session:
        case = TestCase(**body.model_dump())
        session.add(case)
        session.flush()
        crud.audit(session, "test_case_created", actor=actor or "dashboard",
                   via="dashboard", entity="test_case", entity_id=case.id)
        return _case_out(case)


@router.delete("/test_cases/{case_id}")
async def delete_case(case_id: int):
    with db_session() as session:
        case = session.get(TestCase, case_id)
        if case is None:
            raise HTTPException(404)
        session.delete(case)
    return {"ok": True}


class SaveCaseIn(BaseModel):
    name: str = ""
    preferred_answer: str = ""
    expectation_notes: str = ""


@router.post("/responses/{response_id}/save_test_case")
async def save_response_as_case(response_id: int, body: SaveCaseIn, actor: str = ""):
    """Turn a logged (possibly bad) response into a regression test case; an
    operator's corrected reply becomes the preferred answer."""
    with db_session() as session:
        row = session.get(ResponseLog, response_id)
        if row is None:
            raise HTTPException(404)
        case = TestCase(
            name=body.name or f"from response #{response_id}",
            tags="from_response",
            input={"text": row.input_text, "product_slug": row.product_slug},
            expectation_notes=body.expectation_notes,
            preferred_answer=body.preferred_answer,
            source_response_id=response_id,
        )
        session.add(case)
        session.flush()
        crud.audit(session, "test_case_from_response", actor=actor or "dashboard",
                   via="dashboard", entity="response", entity_id=response_id,
                   after={"test_case_id": case.id})
        return _case_out(case)


# ---------------------------------------------------------------------------
# batch runs with auto-eval
# ---------------------------------------------------------------------------

class TestRunIn(BaseModel):
    test_case_ids: list[int] = []   # empty = all
    config_note: str = ""           # e.g. "prompt system_main v3" — free text
    auto_eval: bool = True


async def _auto_eval(question: str, answer: str, preferred: str,
                     notes: str) -> dict:
    prompt, _ = prompt_store.get("auto_eval")
    reference = ""
    if preferred:
        reference += f"\nPreferred answer (from a human operator):\n{preferred}"
    if notes:
        reference += f"\nExpectations:\n{notes}"
    try:
        from pydantic import BaseModel as PB

        class Scores(PB):
            correctness: int
            relevance: int
            completeness: int
            persian_quality: int
            scope: int
            grounding: int
            overall_comment: str = ""

        result = await run_role(
            "evaluation",
            [SystemMessage(content=prompt),
             HumanMessage(content=f"Question:\n{question}\n{reference}\n\n"
                                  f"Assistant answer:\n{answer}")],
            structured=Scores,
        )
        return result.output.model_dump()
    except LLMFailure as e:
        return {"error": str(e)[:200]}


@router.post("/test_runs")
async def run_tests(body: TestRunIn):
    batch_id = secrets.token_hex(8)
    with db_session() as session:
        stmt = select(TestCase)
        if body.test_case_ids:
            stmt = stmt.where(TestCase.id.in_(body.test_case_ids))
        cases = [_case_out(c) for c in session.scalars(stmt)]
    if not cases:
        raise HTTPException(400, "no test cases selected")
    results = []
    for case in cases:
        inp = case["input"]
        sandbox = await _run_sandbox(TestChatIn(
            text=inp.get("text", ""), page_url=inp.get("page_url", ""),
            product_slug=inp.get("product_slug", ""),
            history=inp.get("history", [])))
        auto_eval = {}
        if body.auto_eval and sandbox.get("final_text"):
            auto_eval = await _auto_eval(inp.get("text", ""), sandbox["final_text"],
                                         case["preferred_answer"],
                                         case["expectation_notes"])
        with db_session() as session:
            run = TestRun(
                test_case_id=case["id"], batch_id=batch_id,
                config_note=body.config_note, output_text=sandbox.get("final_text") or "",
                outcome=sandbox.get("outcome") or "",
                model_used=", ".join(sandbox.get("models") or []),
                auto_eval=auto_eval, cost_usd=sandbox.get("cost_usd", 0),
                latency_ms=sandbox.get("latency_ms", 0),
            )
            session.add(run)
            session.flush()
            results.append({"test_case_id": case["id"], "run_id": run.id,
                            "outcome": run.outcome, "auto_eval": auto_eval,
                            "output_text": run.output_text})
    return {"batch_id": batch_id, "results": results}


@router.get("/test_runs")
async def list_runs(test_case_id: int | None = None, batch_id: str = "",
                    page: int = 1, per_page: int = 50):
    with db_session() as session:
        stmt = select(TestRun)
        if test_case_id:
            stmt = stmt.where(TestRun.test_case_id == test_case_id)
        if batch_id:
            stmt = stmt.where(TestRun.batch_id == batch_id)
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = session.scalars(stmt.order_by(desc(TestRun.id))
                               .offset((page - 1) * per_page).limit(per_page))
        return {"data": [
            {"id": r.id, "test_case_id": r.test_case_id, "batch_id": r.batch_id,
             "config_note": r.config_note, "output_text": r.output_text,
             "outcome": r.outcome, "model_used": r.model_used, "auto_eval": r.auto_eval,
             "cost_usd": r.cost_usd, "latency_ms": r.latency_ms,
             "created_at": r.created_at.isoformat()} for r in rows
        ], "total": total}


# ---------------------------------------------------------------------------
# content-gap retest
# ---------------------------------------------------------------------------

@router.post("/content_gaps/{gap_id}/retest")
async def retest_gap(gap_id: int, actor: str = ""):
    """Re-ask the gap's original question; if the agent now answers (instead of
    recording the info as missing again), the content team's fix worked."""
    with db_session() as session:
        gap = session.get(ContentGap, gap_id)
        if gap is None:
            raise HTTPException(404)
        question, slug, page_url = gap.question, gap.product_slug, gap.page_url
    sandbox = await _run_sandbox(TestChatIn(
        text=question, product_slug=slug, page_url=page_url))
    gap_recorded_again = any(
        c.get("tool") == "record_content_gap" for c in sandbox.get("tool_calls") or [])
    result = {
        "answered": sandbox.get("outcome") == "reply" and not gap_recorded_again,
        "outcome": sandbox.get("outcome"),
        "final_text": sandbox.get("final_text"),
        "gap_recorded_again": gap_recorded_again,
        "at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    with db_session() as session:
        gap = session.get(ContentGap, gap_id)
        gap.last_retest = result
        crud.audit(session, "content_gap_retested", actor=actor or "dashboard",
                   via="dashboard", entity="content_gap", entity_id=gap_id,
                   after={"answered": result["answered"]})
    return result

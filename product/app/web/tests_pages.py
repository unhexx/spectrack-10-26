"""Страница испытания грейда в кабинете кандидата.

ТЗ §2.1 «Прохождение тестирования: опрос по отрасли и специализации, выбор
предполагаемого грейда, прохождение теста; результат автоматически определяет
категорию, видимую работодателю». ТЗ §2 «Достаточно подготовить несколько
описаний вакансий и на их основе показать, как система формирует задания».

Вопросы показываются по одному, вернуться к отвеченному нельзя: ответ
сохраняется сразу (обновление страницы не открывает его снова). У judge
кандидат оценивает каждый вариант — «верно» или «неверно». У multi, order
и numeric вводит одно число. Работает без JavaScript.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from product.app.api.deps import db_session
from product.app.db.models import Profile, TestAttempt
from product.app.services.errors import ApiError, fail, hint as catalog_hint
from product.app.services.tests import (
    answer_question,
    attempt_result,
    interval_blocks,
    interval_parts,
    latest_recorded_attempt,
    list_attempts,
    proposed_adjacent,
    public_draft,
    start_attempt,
)
from product.app.web.guards import page_user
from product.app.web.render import assert_csrf, ensure_csrf, page_error, render
from product.matching import assessment

router = APIRouter()
_SAMPLES = Path(__file__).resolve().parents[2] / "matching" / "vacancies" / "samples.json"
_MARKS = {"true", "false"}


def _samples() -> list[dict]:
    """Описания вакансий → план теста, который получит кандидат (ТЗ §2)."""
    if not _SAMPLES.is_file():
        return []
    payload = json.loads(_SAMPLES.read_text(encoding="utf-8"))
    rows = []
    for item in payload.get("vacancies") or []:
        rows.append({**item, "plan": assessment.blueprint(item["grade"])})
    return rows


def _context(request: Request, db: Session, user, error: str = "") -> dict:
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    draft = None
    draft_view = None
    if profile is not None:
        draft = db.scalar(
            select(TestAttempt).where(TestAttempt.profile_id == profile.id, TestAttempt.status == "draft")
        )
        if draft is not None and not draft.form_json:
            draft = None  # черновик без варианта: кнопка «Начать» соберёт вариант заново
        if draft is not None:
            draft_view = public_draft(draft)
    history = list_attempts(db, user)
    offer = proposed_adjacent(db, profile) if profile is not None else ""
    interval_notice = ""
    hint = ""
    recorded = latest_recorded_attempt(db, profile) if profile is not None else None
    outcome_view = None
    if profile is None:
        hint = catalog_hint("survey_required")
    elif offer and interval_blocks(db, profile, offer):
        interval_notice = catalog_hint("grade_interval", **interval_parts(profile, offer))
    if draft is None and recorded is not None:
        result = attempt_result(recorded)
        outcome_view = {
            "score": recorded.score,
            "outcome": recorded.outcome,
            "declared_grade": recorded.declared_grade,
            "proposed_grade": recorded.proposed_grade,
            "confidence": result.get("confidence"),
            "confidence_pct": None if result.get("confidence") is None else round(100 * result["confidence"]),
            "test_status": result.get("status"),
            "position": result.get("position"),
            "grade_estimate": result.get("grade"),
            "n_items": result.get("n_items"),
        }
    question = None
    if draft_view and draft_view["questions"]:
        question = draft_view["questions"][0]
    return {
        "csrf": ensure_csrf(request),
        "error": error,
        "hint": hint,
        "user": user,
        "profile": profile,
        "draft": draft,
        "draft_view": draft_view,
        "question": question,
        "history": history,
        "offer": offer,
        "interval_notice": interval_notice,
        "outcome_view": outcome_view,
        "samples": _samples(),
    }


@router.get("/candidate/test")
def test_page(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    return render(request, "candidate/test.html", _context(request, db, user))


@router.post("/candidate/test/start")
async def start_page(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    form = await request.form()
    grade = str(form.get("grade") or "")
    payload = {"grade": grade} if grade else {}
    try:
        assert_csrf(request, str(form.get("csrf") or ""))
        start_attempt(db, user, payload)
    except ApiError as exc:
        return page_error(request, "candidate/test.html", _context(request, db, user), exc)
    return RedirectResponse("/candidate/test", status_code=303)


@router.post("/candidate/test/answer")
async def answer_page(request: Request, db: Session = Depends(db_session)):
    """Один вопрос: judge — mark_<id> = true|false; числовой тип — поле value."""
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    form = await request.form()
    try:
        assert_csrf(request, str(form.get("csrf") or ""))
        try:
            attempt_id = int(str(form.get("attempt_id") or ""))
        except ValueError:
            fail(404, "not_found", "not_found_attempt")
        qid = str(form.get("qid") or "")
        marks = {key[5:]: str(value) for key, value in form.items() if key.startswith("mark_")}
        if marks:
            if any(value not in _MARKS for value in marks.values()):
                fail(422, "answer_unknown")
            chosen = [choice for choice, value in marks.items() if value == "true"]
            answer_question(db, user, attempt_id, qid, chosen, marked=set(marks))
        else:
            raw = form.get("value")
            if raw is None or str(raw).strip() == "":
                fail(422, "answer_unknown")
            answer_question(db, user, attempt_id, qid, str(raw), marked=None)
    except ApiError as exc:
        return page_error(request, "candidate/test.html", _context(request, db, user), exc)
    return RedirectResponse("/candidate/test", status_code=303)

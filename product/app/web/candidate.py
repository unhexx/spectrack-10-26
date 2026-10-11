"""Кабинет соискателя. Разметку теста и приглашений поздние срезы кладут в свои шаблоны.

ТЗ §2.1 «Личный кабинет Кандидат».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from product.app.api.deps import db_session
from product.app.api.resumes import read_resume_upload
from product.app.services.errors import ApiError
from product.app.services.profiles import cabinet_payload, save_profile
from product.app.services.resumes import apply_resume
from product.app.web.guards import page_user
from product.app.web.render import assert_csrf, ensure_csrf, page_error, render

router = APIRouter()


def _cabinet_context(request: Request, db: Session, user, error: str = "") -> dict:
    body = cabinet_payload(db, user)
    body["csrf"] = ensure_csrf(request)
    body["error"] = error
    body["user"] = user
    link_error = request.session.pop("link_error", None)
    link_hint = request.session.pop("link_hint", None)
    if isinstance(link_error, str) and link_error and not error:
        body["error"] = link_error
    if isinstance(link_hint, str) and link_hint:
        body["hint"] = link_hint
    return body


@router.get("/candidate")
def cabinet(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    return render(request, "candidate/cabinet.html", _cabinet_context(request, db, user))


@router.post("/candidate/profile")
def save_cabinet(
    request: Request,
    full_name: str = Form(""),
    industry: str = Form("it"),
    specialization: str = Form(""),
    grade: str = Form(""),
    team_role: str = Form(""),
    work_format: str = Form(""),
    experience: str = Form(""),
    external_id: str = Form(""),
    csrf: str = Form(""),
    publication: str | None = Form(None),
    show_name: str | None = Form(None),
    show_experience: str | None = Form(None),
    show_achievements: str | None = Form(None),
    stack: list[str] = Form(default=[]),
    soft_skills: list[str] = Form(default=[]),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    payload = {
        "full_name": full_name,
        "industry": industry,
        "specialization": specialization,
        "grade": grade,
        "team_role": team_role,
        "work_format": work_format,
        "experience": experience,
        "external_id": external_id,
        "stack": stack,
        "soft_skills": soft_skills,
        "publication": publication in {"1", "on", "true", "True"},
        "show_name": show_name in {"1", "on", "true", "True"},
        "show_experience": show_experience in {"1", "on", "true", "True"},
        "show_achievements": show_achievements in {"1", "on", "true", "True"},
    }
    try:
        assert_csrf(request, csrf)
        save_profile(db, user, payload)
    except ApiError as exc:
        return page_error(request, "candidate/cabinet.html", _cabinet_context(request, db, user), exc)
    return RedirectResponse("/candidate", status_code=303)


@router.post("/candidate/resume")
async def upload_resume_page(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    try:
        payload, csrf, stopped = await read_resume_upload(request)
        if not stopped:
            assert_csrf(request, csrf)
        apply_resume(db, user, payload, too_large=stopped)
    except ApiError as exc:
        return page_error(request, "candidate/cabinet.html", _cabinet_context(request, db, user), exc)
    return RedirectResponse("/candidate", status_code=303)

"""Анкета соискателя и пустой слот достижений.

ТЗ §2.1 «Заполнение профиля и резюме», «Настройки: приватность, согласие на обработку и публикацию»; ТЗ §3.5 «152-ФЗ».
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from sqlalchemy import select

from product.app.api.deps import db_session, require_candidate
from product.app.db.models import Profile, User
from product.app.services.fsp import achievements_for
from product.app.services.errors import example, fail, response_example
from product.app.services.periodic import complete_offer, empty_offer, offer_view
from product.app.services.profile_ref import own_profile_id
from product.app.services.profiles import get_own_profile, profile_payload, save_profile

router = APIRouter(tags=["candidate"])

_PROFILE = {
    "full_name": "Иван Синтетиков",
    "industry": "it",
    "specialization": "backend",
    "grade": "middle",
    "stack": ["python"],
    "team_role": "разработчик",
    "work_format": "удалённо",
    "experience": "3 года",
    "soft_skills": ["коммуникация"],
    "external_id": "",
    "publication": True,
}
_NOT_FOUND = example("not_found", "not_found_profile")
_CLOSED = example("closed_list")
_PERIODIC = {
    "vacancy_id": 1,
    "title": "Backend middle",
    "prompt": "Назовите один заголовок идемпотентного POST.",
    "offered_at": "2026-10-07T12:00:00Z",
    "completed_at": "",
    "line": "ещё не сдавал",
}
_PERIODIC_DONE = {
    "vacancy_id": None,
    "title": "",
    "prompt": "",
    "offered_at": "",
    "completed_at": "",
    "line": "последнее задание 0 дней назад",
}
_TASK_MISSING = example("not_found", "not_found_periodic")


@router.get(
    "/candidate/profile",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "profile_id": 1,
                        "full_name": "Иван Синтетиков",
                        "email": "seeker@example.com",
                        "industry": "it",
                        "specialization": "backend",
                        "grade": "middle",
                        "category_id": None,
                        "publication": True,
                    }
                }
            }
        },
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_candidate")}}},
        404: {"content": {"application/json": {"example": _NOT_FOUND}}},
    },
)
def read_profile(user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    profile = get_own_profile(db, user)
    return profile_payload(db, user, profile)


@router.put(
    "/candidate/profile",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "profile_id": 1,
                        "industry": "it",
                        "category_id": None,
                        "grade": "middle",
                        "specialization": "backend",
                    }
                }
            }
        },
        409: {"content": {"application/json": {"example": example("grade_via_test")}}},
        422: response_example(422, _CLOSED),
    },
)
def write_profile(
    payload: dict = Body(..., examples={"survey": {"value": _PROFILE}}),
    user: User = Depends(require_candidate),
    db: Session = Depends(db_session),
):
    return save_profile(db, user, payload)


@router.get(
    "/candidate/achievements",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {"display_name": "", "rank": "", "results": []}
                }
            }
        },
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_candidate")}}},
    },
)
def read_achievements(user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    external_id = db.scalar(select(Profile.external_id).where(Profile.user_id == user.id))
    return achievements_for(db, external_id)


@router.get(
    "/candidate/periodic-task",
    responses={
        200: {"content": {"application/json": {"example": _PERIODIC}}},
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_candidate")}}},
    },
)
def read_periodic_task(user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    profile_id = own_profile_id(db, user)
    if profile_id is None:
        return empty_offer()
    return offer_view(db, profile_id)


@router.post(
    "/candidate/periodic-task",
    responses={
        200: {"content": {"application/json": {"example": _PERIODIC_DONE}}},
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_candidate")}}},
        404: {"content": {"application/json": {"example": _TASK_MISSING}}},
    },
)
def complete_periodic_task(user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    profile_id = own_profile_id(db, user)
    if profile_id is None:
        fail(404, "not_found", "not_found_periodic")
    return complete_offer(db, profile_id)

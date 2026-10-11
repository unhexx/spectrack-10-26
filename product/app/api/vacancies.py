"""Вакансия работодателя и отклик соискателя.

ТЗ §2.1 «Просмотр вакансий и отклик», «Публикация вакансий»; ТЗ §6 критерий 6 (дополнительный функционал).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from product.app.api.deps import db_session, require_candidate, require_employer
from product.app.db.models import User
from product.app.services.errors import example, response_example
from product.app.services.vacancies import (
    apply_to_vacancy,
    create_vacancy,
    decline_application,
    list_candidate_applications,
    list_employer_applications,
    list_employer_vacancies,
    list_published,
    open_application,
    set_employer_application_status,
    update_vacancy,
)

router = APIRouter(tags=["vacancies"])

_VACANCY = {
    "id": 1,
    "category_id": "backend:middle",
    "title": "Backend middle",
    "description": "Платежи, без дежурств",
    "company_name": "Синтетика",
    "contact_channel": "telegram: @sintetika",
    "salary_min": 180000,
    "salary_max": 250000,
    "salary_currency": "RUB",
    "task_prompt": "Назовите один заголовок идемпотентного POST.",
    "status": "published",
    "created_at": "2026-10-07T12:00:00Z",
    "updated_at": "2026-10-07T12:00:00Z",
}
_BODY = {
    "title": "Backend middle",
    "description": "Платежи, без дежурств",
    "category_id": "backend:middle",
    "company_name": "Синтетика",
    "contact_channel": "telegram: @sintetika",
    "salary_min": 180000,
    "salary_max": 250000,
    "salary_currency": "RUB",
    "task_prompt": "Назовите один заголовок идемпотентного POST.",
}
_APPLICATION = {
    "id": 1,
    "vacancy_id": 1,
    "profile_id": 12,
    "title": "Backend middle",
    "company_name": "Синтетика",
    "contact_channel": "telegram: @sintetika",
    "salary_min": 180000,
    "salary_max": 250000,
    "salary_currency": "RUB",
    "message": "Готов обсудить платежи",
    "task_answer": "Idempotency-Key",
    "status": "sent",
    "created_at": "2026-10-07T12:00:00Z",
    "updated_at": "2026-10-07T12:00:00Z",
}
_ACCEPTED = {**_APPLICATION, "status": "accepted", "candidate_email": "seeker@example.com"}
_APPLY = {"message": "Готов обсудить платежи", "task_answer": "Idempotency-Key"}
_DENIED = example("unauthorized")
_FORBIDDEN = example("forbidden", "forbidden_employer")
_SEEKER_FORBIDDEN = example("forbidden", "forbidden_candidate")
_RANGE = example("salary_range")
_MISSING = example("not_found", "not_found_vacancy")
_MISSING_OWN = example("not_found", "not_found_own_vacancy")
_MISSING_APP = example("not_found", "not_found_application")


@router.post(
    "/employer/vacancies",
    status_code=201,
    responses={
        201: {"content": {"application/json": {"example": _VACANCY}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
        422: response_example(422, _RANGE),
    },
)
def publish_vacancy(
    payload: dict = Body(..., examples={"vacancy": {"value": _BODY}}),
    user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    return create_vacancy(db, user, payload)


@router.post(
    "/employer/vacancies/{vacancy_id}",
    responses={
        200: {"content": {"application/json": {"example": _VACANCY}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
        404: {"content": {"application/json": {"example": _MISSING_OWN}}},
        422: response_example(422, _RANGE),
    },
)
def save_vacancy(
    vacancy_id: int,
    payload: dict = Body(..., examples={"vacancy": {"value": _BODY}}),
    user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    return update_vacancy(db, user, vacancy_id, payload)


@router.get(
    "/employer/vacancies",
    responses={
        200: {"content": {"application/json": {"example": [_VACANCY]}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
    },
)
def employer_vacancies(user: User = Depends(require_employer), db: Session = Depends(db_session)):
    return list_employer_vacancies(db, user)


@router.get(
    "/employer/applications",
    responses={
        200: {"content": {"application/json": {"example": [_ACCEPTED]}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
    },
)
def employer_applications(user: User = Depends(require_employer), db: Session = Depends(db_session)):
    return list_employer_applications(db, user)


@router.post(
    "/employer/applications/{application_id}/open",
    responses={
        200: {"content": {"application/json": {"example": {**_APPLICATION, "status": "seen"}}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
        404: {"content": {"application/json": {"example": _MISSING_APP}}},
    },
)
def read_application(
    application_id: int,
    user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    return open_application(db, user, application_id)


@router.post(
    "/employer/applications/{application_id}/accept",
    responses={
        200: {"content": {"application/json": {"example": _ACCEPTED}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
        404: {"content": {"application/json": {"example": _MISSING_APP}}},
        409: {"content": {"application/json": {"example": example("application_closed")}}},
    },
)
def accept_application(
    application_id: int,
    user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    return set_employer_application_status(db, user, application_id, "accepted")


@router.post(
    "/employer/applications/{application_id}/decline",
    responses={
        200: {"content": {"application/json": {"example": {**_APPLICATION, "status": "declined"}}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _FORBIDDEN}}},
        404: {"content": {"application/json": {"example": _MISSING_APP}}},
        409: {"content": {"application/json": {"example": example("application_closed")}}},
    },
)
def decline_for_employer(
    application_id: int,
    user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    return set_employer_application_status(db, user, application_id, "declined")


@router.get(
    "/vacancies",
    responses={
        200: {"content": {"application/json": {"example": [_VACANCY]}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _SEEKER_FORBIDDEN}}},
    },
)
def published_vacancies(_user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    return list_published(db)


@router.post(
    "/vacancies/{vacancy_id}/apply",
    status_code=201,
    responses={
        201: {"content": {"application/json": {"example": _APPLICATION}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _SEEKER_FORBIDDEN}}},
        404: {"content": {"application/json": {"example": _MISSING}}},
        409: {"content": {"application/json": {"example": example("application_open")}}},
        422: response_example(422, example("task_answer")),
    },
)
def apply(
    vacancy_id: int,
    payload: dict = Body(..., examples={"apply": {"value": _APPLY}}),
    user: User = Depends(require_candidate),
    db: Session = Depends(db_session),
):
    return apply_to_vacancy(db, user, vacancy_id, payload)


@router.get(
    "/candidate/applications",
    responses={
        200: {"content": {"application/json": {"example": [_APPLICATION]}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _SEEKER_FORBIDDEN}}},
    },
)
def candidate_applications(user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    return list_candidate_applications(db, user)


@router.post(
    "/candidate/applications/{application_id}/decline",
    responses={
        200: {"content": {"application/json": {"example": {**_APPLICATION, "status": "declined"}}}},
        401: {"content": {"application/json": {"example": _DENIED}}},
        403: {"content": {"application/json": {"example": _SEEKER_FORBIDDEN}}},
        404: {"content": {"application/json": {"example": _MISSING_APP}}},
        409: {"content": {"application/json": {"example": example("application_closed")}}},
    },
)
def withdraw(
    application_id: int,
    user: User = Depends(require_candidate),
    db: Session = Depends(db_session),
):
    return decline_application(db, user, application_id)

"""Категория соискателя и выдача работодателя.

ТЗ §2.1 «Категория и грейд», «Поиск по банку кандидатов», «Работа с подборкой»; ТЗ §2.1 «Функциональные требования к поиску и подбору».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from product.app.api.deps import db_session, require_candidate, require_employer
from product.app.db.models import User
from product.app.services.categories import employer_candidates, latest_assignment, parse_has_fsp
from product.app.services.category_hint import category_hint
from product.app.services.errors import example, fail, response_example
from product.app.services.profiles import get_own_profile

router = APIRouter(tags=["category"])

_CATEGORY = {
    "profile_id": 1,
    "category_id": "backend:middle",
    "rule_version": "v1",
    "explanation": "Опрос: специализация backend, грейд middle. Тест ещё не сдан. Правила v1.",
    "test_score": None,
    "industry": "it",
}
_MISSING = example("category_id")
_CARD = {
    "profile_id": 1,
    "full_name": "Иван Синтетиков",
    "industry": "it",
    "category_id": "backend:middle",
    "explanation": "Опрос: специализация backend, грейд middle. Тест: заявленный грейд подтверждён, балл 67. Правила v1.",
    "list_reason": "Категория backend:middle. Тест сдан, потребность без уточнений.",
    "rank_key": "1-067-0000-0-00000001",
    "test_score": 67,
    "grade_status": "подтверждён",
    "stack": ["python"],
    "team_role": "разработчик",
    "work_format": "удалённо",
    "experience": "синтетический стаж",
    "fsp_rank": "",
    "achievements": [],
}
_HINT = [
    {"category_id": "backend:senior", "spare": False, "count": 3},
    {"category_id": "backend:middle", "spare": True, "count": 5},
]


@router.get(
    "/candidate/category",
    responses={
        200: {"content": {"application/json": {"example": _CATEGORY}}},
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_candidate")}}},
        404: {"content": {"application/json": {"example": example("not_found", "not_found_category")}}},
    },
)
def read_category(user: User = Depends(require_candidate), db: Session = Depends(db_session)):
    profile = get_own_profile(db, user)
    row = latest_assignment(db, profile.id)
    if row is None:
        fail(404, "not_found", "not_found_category")
    return {
        "profile_id": profile.id,
        "category_id": row.category_id,
        "rule_version": row.rule_version,
        "explanation": row.explanation,
        "test_score": row.test_score,
        "rank_key": row.rank_key,
        "industry": profile.industry,
    }


@router.get(
    "/employer/candidates",
    responses={
        200: {"content": {"application/json": {"example": [_CARD]}}},
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_employer")}}},
        422: response_example(422, _MISSING),
    },
)
def read_candidates(
    category_id: str | None = Query(default=None, description="Категория specialization:grade; пусто — поиск по всей базе"),
    specialization: str = Query(default="", description="Фильтр по специализации при поиске по всей базе"),
    grade: str = Query(default="", description="Фильтр по грейду при поиске по всей базе"),
    stack: list[str] = Query(default=[]),
    team_role: str = Query(default=""),
    work_format: str = Query(default=""),
    has_fsp: str | None = Query(default=None),
    q: str = Query(default=""),
    _user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    """Подборка по категории или поиск по всему банку кандидатов (ТЗ §2.1
    «Поиск по банку кандидатов»). Без категории выдаются все опубликованные
    профили, отфильтрованные по специализации, грейду, стеку, роли, формату и
    достижениям ФСП, в том же порядке ранжирования."""
    return employer_candidates(
        db,
        category_id=category_id or "",
        specialization=specialization,
        grade=grade,
        stack=stack,
        team_role=team_role,
        work_format=work_format,
        has_fsp=parse_has_fsp(has_fsp),
        q=q,
    )


@router.get(
    "/employer/category-hint",
    responses={
        200: {"content": {"application/json": {"example": _HINT}}},
        401: {"content": {"application/json": {"example": example("unauthorized")}}},
        403: {"content": {"application/json": {"example": example("forbidden", "forbidden_employer")}}},
    },
)
def read_category_hint(
    q: str = Query(default="", description="Текст потребности"),
    _user: User = Depends(require_employer),
    db: Session = Depends(db_session),
):
    """Две строки на найденную специализацию и счётчик опубликованных.

    Подсказка не выбирает категорию и не сужает выдачу.
    """
    return category_hint(db, q)

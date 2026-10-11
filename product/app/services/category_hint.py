"""Подсказка категории по тексту потребности.

Совпадение — равенство токена из закрытого списка, не морфология.
Счётчик — опубликованные профили с этой категорией на последнем назначении.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from product.app.db.models import Consent, Profile
from product.app.services.categories import latest_assignment
from product.matching.rules import load_rules

# Ключи — specializations/grades из правил. Значения в v1.json не лежат.
SPECIALIZATIONS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "бэкенд", "бэкенда", "бекенд", "бекенда"),
    "frontend": ("frontend", "фронтенд", "фронтенда", "фронт"),
    "mobile": ("mobile", "мобильный", "мобильная", "мобильной"),
    "data": ("data", "данные", "данных"),
    "security": ("security", "безопасность", "безопасности"),
    "devops": ("devops", "девопс"),
}
GRADES: dict[str, tuple[str, ...]] = {
    "junior": ("junior", "джуниор", "джун", "младший"),
    "middle": ("middle", "мидл", "миддл", "средний"),
    "senior": ("senior", "сеньор", "старший"),
    "lead": ("lead", "лид", "ведущий"),
}
_GRADES = ("junior", "middle", "senior", "lead")

_SPEC_OF = {word: spec for spec, words in SPECIALIZATIONS.items() for word in words}
_GRADE_OF = {word: grade for grade, words in GRADES.items() for word in words}


def spare_grade(primary: str) -> str:
    if primary == "junior":
        return "middle"
    return _GRADES[_GRADES.index(primary) - 1]


def _tokens(q: str) -> set[str]:
    found: set[str] = set()
    for piece in q.casefold().split():
        start = 0
        end = len(piece)
        while start < end and not piece[start].isalpha():
            start += 1
        while end > start and not piece[end - 1].isalpha():
            end -= 1
        token = piece[start:end]
        if token:
            found.add(token)
    return found


def _published_counts(session: Session) -> dict[str, int]:
    """Тот же обход, что у выдачи: все профили, согласие publication, последнее назначение."""
    counts: dict[str, int] = {}
    profiles = session.scalars(select(Profile)).all()
    for profile in profiles:
        consent = session.scalar(select(Consent).where(Consent.profile_id == profile.id))
        if consent is None or not consent.publication:
            continue
        assignment = latest_assignment(session, profile.id)
        if assignment is None:
            continue
        counts[assignment.category_id] = counts.get(assignment.category_id, 0) + 1
    return counts


def category_hint(session: Session, q: str) -> list[dict]:
    if not q or not q.strip():
        return []
    tokens = _tokens(q)
    found_specs = {_SPEC_OF[token] for token in tokens if token in _SPEC_OF}
    if not found_specs:
        return []
    rules = load_rules()
    specs = [spec for spec in rules["specializations"] if spec in found_specs]
    if not specs:
        return []
    found_grades = {_GRADE_OF[token] for token in tokens if token in _GRADE_OF}
    named = [grade for grade in rules["grades"] if grade in found_grades]
    primary = named[-1] if named else "middle"
    second = spare_grade(primary)
    counts = _published_counts(session)
    rows: list[dict] = []
    for spec in specs:
        for grade, is_spare in ((primary, False), (second, True)):
            category_id = f"{spec}:{grade}"
            rows.append(
                {
                    "category_id": category_id,
                    "spare": is_spare,
                    "count": counts.get(category_id, 0),
                }
            )
    return rows

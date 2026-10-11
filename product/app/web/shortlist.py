"""Подборка работодателя. Новый фильтр не стирает уже показанный блок.

ТЗ §2.1 «Работа с подборкой… обоснование попадания», «фильтрация и уточнение запроса без потери ранее полученной подборки».
ТЗ §2.1 «Поиск по банку кандидатов»: без категории форма ищет по всей базе
с фильтрами по специализации и грейду.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from product.app.api.deps import db_session
from product.app.services.categories import employer_candidates, parse_has_fsp
from product.app.services.category_hint import category_hint
from product.app.services.errors import ApiError, hint as catalog_hint
from product.app.web.guards import page_user
from product.app.web.render import ensure_csrf, render
from product.matching.rules import load_rules

router = APIRouter()


@router.get("/employer/shortlist")
def shortlist_page(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    rules = load_rules()
    params = request.query_params
    category_id = params.get("category_id") or ""
    specialization = params.get("specialization") or ""
    grade = params.get("grade") or ""
    partial = request.headers.get("hx-request") == "true"
    ctx = {
        "csrf": ensure_csrf(request),
        "user": user,
        "rules": rules,
        "title": "",
        "cards": [],
        "error": "",
        "hint": "",
        "category_hints": [],
        "show_block": False,
    }
    q = params.get("q") or ""
    # Отправленная форма (любой параметр) — уже запрос; пустая категория = вся база.
    if category_id or partial or len(params):
        try:
            cards = employer_candidates(
                db,
                category_id=category_id,
                specialization=specialization,
                grade=grade,
                stack=params.getlist("stack"),
                team_role=params.get("team_role") or "",
                work_format=params.get("work_format") or "",
                has_fsp=parse_has_fsp(params.get("has_fsp")),
                q=q,
            )
            ctx["title"] = _title(category_id, specialization, grade)
            ctx["cards"] = cards
            if not cards:
                ctx["hint"] = catalog_hint("hint_shortlist_empty")
        except ApiError as exc:
            ctx["title"] = "Подборка"
            ctx["error"] = exc.message
        if q.strip():
            ctx["category_hints"] = category_hint(db, q)
        ctx["show_block"] = True
        if partial:
            return render(request, "employer/_shortlist_block.html", ctx)
    return render(request, "employer/shortlist.html", ctx)


def _title(category_id: str, specialization: str, grade: str) -> str:
    """Заголовок блока: подборка категории или поиск по базе с его фильтрами."""
    if category_id:
        return f"Подборка {category_id}"
    scope = " · ".join(part for part in (specialization, grade) if part)
    return f"Поиск по базе: {scope}" if scope else "Поиск по всей базе"

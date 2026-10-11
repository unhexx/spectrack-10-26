"""Периодическое задание: одна вакансия раз в 30 дней, сдача без балла.

Интервал грейда здесь не читается. Текст ответа в базу не пишется.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from product.app.db.models import EmployerTask, PeriodicTask, Vacancy
from product.app.log import log_event
from product.app.services.clock import days_elapsed, days_remaining, utc_now, whole_days_since
from product.app.services.errors import fail

TASK_OFFER_INTERVAL_DAYS = 30


def task_line(completed_at: str | None) -> str:
    if completed_at is None:
        return "ещё не сдавал"
    return f"последнее задание {whole_days_since(completed_at)} дней назад"


def freshness_bonus(completed_at: str | None) -> int:
    if not completed_at:
        return 0
    if days_elapsed(completed_at, TASK_OFFER_INTERVAL_DAYS):
        return 0
    return 1


def empty_offer() -> dict:
    return {
        "vacancy_id": None,
        "title": "",
        "prompt": "",
        "offered_at": "",
        "completed_at": "",
        "line": task_line(None),
    }


def notice_for(view: dict) -> str:
    """Фраза секции кабинета, когда живого предложения нет."""
    if view.get("vacancy_id"):
        return ""
    if view.get("completed_at"):
        left = days_remaining(view["completed_at"], TASK_OFFER_INTERVAL_DAYS)
        return f"Следующее задание через {left} дней."
    if view.get("offered_at"):
        left = days_remaining(view["offered_at"], TASK_OFFER_INTERVAL_DAYS)
        return f"Следующее задание через {left} дней."
    return "Заданий сейчас нет."


def _latest(db: Session, profile_id: int) -> PeriodicTask | None:
    return db.scalar(
        select(PeriodicTask)
        .where(PeriodicTask.profile_id == profile_id)
        .order_by(PeriodicTask.id.desc())
        .limit(1)
    )


def _open_row(db: Session, profile_id: int) -> PeriodicTask | None:
    return db.scalar(
        select(PeriodicTask).where(
            PeriodicTask.profile_id == profile_id,
            PeriodicTask.completed_at.is_(None),
        )
    )


def _last_completed_at(db: Session, profile_id: int) -> str | None:
    return db.scalar(
        select(func.max(PeriodicTask.completed_at)).where(
            PeriodicTask.profile_id == profile_id,
            PeriodicTask.completed_at.is_not(None),
        )
    )


def _live(db: Session, vacancy_id: int) -> tuple[Vacancy, EmployerTask] | None:
    vacancy = db.get(Vacancy, vacancy_id)
    if vacancy is None or vacancy.status != "published":
        return None
    task = db.scalar(select(EmployerTask).where(EmployerTask.vacancy_id == vacancy.id))
    if task is None:
        return None
    return vacancy, task


def _pick(db: Session, profile_id: int) -> Vacancy | None:
    rows = db.scalars(
        select(Vacancy)
        .join(EmployerTask, EmployerTask.vacancy_id == Vacancy.id)
        .where(Vacancy.status == "published")
        .order_by(Vacancy.id)
    ).all()
    if not rows:
        return None
    return rows[profile_id % len(rows)]


def _add_open(db: Session, profile_id: int, vacancy_id: int) -> PeriodicTask:
    row = PeriodicTask(
        profile_id=profile_id,
        vacancy_id=vacancy_id,
        offered_at=utc_now(),
        completed_at=None,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Гонка двух вставок: открытая строка уже есть, 409 не отдаём.
        existing = _open_row(db, profile_id)
        if existing is None:
            raise
        return existing
    log_event("periodic_offered", str(profile_id))
    return row


def current_offer(db: Session, profile_id: int) -> PeriodicTask | None:
    latest = _latest(db, profile_id)
    if latest is None:
        vacancy = _pick(db, profile_id)
        if vacancy is None:
            return None
        return _add_open(db, profile_id, vacancy.id)
    anchor = latest.completed_at or latest.offered_at
    if not days_elapsed(anchor, TASK_OFFER_INTERVAL_DAYS):
        if latest.completed_at is not None:
            return None
        if _live(db, latest.vacancy_id) is None:
            return None
        return latest
    # Пропуск не пишем как сдачу: иначе он дал бы бонус.
    if latest.completed_at is None:
        db.delete(latest)
        db.flush()
    vacancy = _pick(db, profile_id)
    if vacancy is None:
        db.commit()
        return None
    return _add_open(db, profile_id, vacancy.id)


def _raw_view(db: Session, profile_id: int) -> dict:
    offer = current_offer(db, profile_id)
    line = task_line(_last_completed_at(db, profile_id))
    if offer is not None:
        live = _live(db, offer.vacancy_id)
        if live is not None:
            vacancy, task = live
            return {
                "vacancy_id": vacancy.id,
                "title": vacancy.title,
                "prompt": task.prompt,
                "offered_at": offer.offered_at,
                "completed_at": "",
                "line": line,
            }
    latest = _latest(db, profile_id)
    if (
        latest is not None
        and latest.completed_at
        and not days_elapsed(latest.completed_at, TASK_OFFER_INTERVAL_DAYS)
    ):
        return {
            "vacancy_id": None,
            "title": "",
            "prompt": "",
            "offered_at": "",
            "completed_at": latest.completed_at,
            "line": line,
        }
    if (
        latest is not None
        and latest.completed_at is None
        and not days_elapsed(latest.offered_at, TASK_OFFER_INTERVAL_DAYS)
    ):
        return {
            "vacancy_id": None,
            "title": "",
            "prompt": "",
            "offered_at": latest.offered_at,
            "completed_at": "",
            "line": line,
        }
    return {
        "vacancy_id": None,
        "title": "",
        "prompt": "",
        "offered_at": "",
        "completed_at": "",
        "line": line,
    }


def task_state(db: Session, profile_id: int) -> tuple[dict, str]:
    raw = _raw_view(db, profile_id)
    notice = notice_for(raw)
    if raw["vacancy_id"]:
        return raw, ""
    return {
        "vacancy_id": None,
        "title": "",
        "prompt": "",
        "offered_at": "",
        "completed_at": "",
        "line": raw["line"],
    }, notice


def offer_view(db: Session, profile_id: int) -> dict:
    view, _notice = task_state(db, profile_id)
    return view


def complete_offer(db: Session, profile_id: int) -> dict:
    row = _open_row(db, profile_id)
    if row is None or _live(db, row.vacancy_id) is None:
        fail(404, "not_found", "not_found_periodic")
    row.completed_at = utc_now()
    db.commit()
    log_event("periodic_completed", str(profile_id))
    return offer_view(db, profile_id)

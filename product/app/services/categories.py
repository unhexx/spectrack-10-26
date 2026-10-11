"""Сохраняет назначение категории и собирает выдачу работодателя.

Правила живут в product.matching. Здесь только запись и фильтр чтения.
Пересчёт вставляет новую строку, `latest_assignment` берёт max(id) той же
`rule_version`. Без `publication` карточки в выдаче нет.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from product.app.db.models import CategoryAssignment as StoredCategory
from product.app.db.models import Consent, FspAchievement, FspParticipant, PeriodicTask, Profile, TestAttempt
from product.app.db.session import sqlite_int
from product.app.services.clock import utc_now
from product.app.services.errors import fail
from product.app.services.periodic import freshness_bonus, task_line
from product.matching.category import assign_category, has_prize_place
from product.matching.need import evaluate_need, haystack_text, neighbor_phrase
from product.matching.rules import load_rules, rule_version
from product.matching.types import Need, NeedFit, ProfileView


def recompute(session: Session, profile_id: int) -> None:
    """Балл в назначение берётся с последней сдачи той же пары специализация×записанный грейд.

    PDF без такой сдачи оставляет `test_score` пустым.
    """
    session.flush()
    profile = session.get(Profile, profile_id)
    if profile is None:
        return
    fsp_count = 0
    has_prize = False
    if profile.external_id:
        places = list(
            session.scalars(
                select(FspAchievement.place).where(FspAchievement.external_id == profile.external_id)
            ).all()
        )
        fsp_count = len(places)
        has_prize = has_prize_place(places)
    attempt = session.scalar(
        select(TestAttempt)
        .where(
            TestAttempt.profile_id == profile.id,
            TestAttempt.status == "submitted",
            TestAttempt.specialization == profile.specialization,
            TestAttempt.declared_grade == profile.grade,
            TestAttempt.score.is_not(None),
        )
        .order_by(TestAttempt.id.desc())
        .limit(1)
    )
    view = ProfileView(
        profile_id=profile.id,
        specialization=profile.specialization,
        grade=profile.grade,
        test_score=None if attempt is None else attempt.score,
        test_outcome=None if attempt is None else attempt.outcome,
        fsp_confirmed_count=fsp_count,
        fsp_has_prize=has_prize,
    )
    decision = assign_category(view, rule_version())
    session.add(
        StoredCategory(
            profile_id=profile.id,
            category_id=decision.category_id,
            rule_version=decision.rule_version,
            explanation=decision.explanation,
            test_score=decision.test_score,
            rank_key=decision.rank_key,
            scored_at=utc_now(),
            test_attempt_id=None if attempt is None else attempt.id,
        )
    )
    session.flush()


def latest_assignment(session: Session, profile_id: int) -> StoredCategory | None:
    return session.scalar(
        select(StoredCategory)
        .where(
            StoredCategory.profile_id == profile_id,
            StoredCategory.rule_version == rule_version(),
        )
        .order_by(StoredCategory.id.desc())
        .limit(1)
    )


def require_category(category_id: str) -> str:
    rules = load_rules()
    parts = category_id.split(":")
    if (
        len(parts) != 2
        or parts[0] not in rules["specializations"]
        or parts[1] not in rules["grades"]
        or "/" in category_id
    ):
        fail(422, "category_id")
    return category_id


def parse_has_fsp(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    fail(422, "closed_list", "closed_list_has_fsp")


def _closed(value: str, allowed: list[str], code: str) -> None:
    if value not in allowed:
        fail(
            422,
            code,
            "closed_list",
            what="фильтр подборки",
            allowed=", ".join(allowed),
        )


def _need(
    *,
    category_id: str,
    stack: list[str],
    team_role: str,
    work_format: str,
    has_fsp: bool | None,
    q: str,
) -> Need:
    return Need(
        category_id=category_id,
        stack=tuple(item for item in stack if item),
        team_role=team_role,
        work_format=work_format,
        has_fsp=has_fsp,
        q=q,
    )


def published_rows(session: Session) -> list[tuple[Profile, Consent, StoredCategory]]:
    """Опубликованные профили с последним назначением. Выдача и подсказка ходят сюда."""
    rows: list[tuple[Profile, Consent, StoredCategory]] = []
    for profile in session.scalars(select(Profile)).all():
        consent = session.scalar(select(Consent).where(Consent.profile_id == profile.id))
        if consent is None or not consent.publication:
            continue
        assignment = latest_assignment(session, profile.id)
        if assignment is None:
            continue
        rows.append((profile, consent, assignment))
    return rows


def _profile_matches(
    session: Session,
    profile: Profile,
    assignment: StoredCategory,
    need: Need,
    *,
    mention_achievements: bool = True,
) -> tuple[NeedFit, list[str], list[FspAchievement], str]:
    labels = json.loads(profile.stack_json or "[]")
    achievements: list[FspAchievement] = []
    rank = ""
    if profile.external_id:
        rank_row = session.get(FspParticipant, profile.external_id)
        if rank_row is not None:
            rank = rank_row.rank
        achievements = list(
            session.scalars(
                select(FspAchievement).where(FspAchievement.external_id == profile.external_id)
            ).all()
        )
    haystack = haystack_text(
        specialization=profile.specialization,
        grade=profile.grade,
        stack=labels,
        team_role=profile.team_role,
        work_format=profile.work_format,
        experience=profile.experience or "",
    )
    fit = evaluate_need(
        assigned_category_id=assignment.category_id,
        test_score=assignment.test_score,
        stack=labels,
        team_role=profile.team_role,
        work_format=profile.work_format,
        fsp_count=len(achievements),
        haystack=haystack,
        need=need,
        rank_key=assignment.rank_key,
        mention_achievements=mention_achievements,
    )
    return fit, labels, achievements, rank


def visible_in_category(session: Session, profile_id: int, category_id: str) -> bool:
    """Та же потребность без уточнений стека: карточка либо в категории, либо нет."""
    category_id = require_category(category_id)
    if sqlite_int(profile_id) is None:
        return False
    profile = session.get(Profile, profile_id)
    if profile is None:
        return False
    consent = session.scalar(select(Consent).where(Consent.profile_id == profile.id))
    if consent is None or not consent.publication:
        return False
    assignment = latest_assignment(session, profile.id)
    if assignment is None:
        return False
    fit, _labels, _achievements, _rank = _profile_matches(
        session,
        profile,
        assignment,
        _need(
            category_id=category_id,
            stack=[],
            team_role="",
            work_format="",
            has_fsp=None,
            q="",
        ),
    )
    return fit.matched


def employer_candidates(
    session: Session,
    *,
    category_id: str = "",
    specialization: str = "",
    grade: str = "",
    stack: list[str],
    team_role: str,
    work_format: str,
    has_fsp: bool | None,
    q: str,
) -> list[dict]:
    """Подборка по категории или поиск по всему банку кандидатов.

    ТЗ §2.1 «Работа с подборкой»: категория задаёт подборку.
    ТЗ §2.1 «Поиск по банку кандидатов: доступ ко всей базе с фильтрацией по
    специализации, грейду, категории, стеку и наличию подтверждённых
    достижений ФСП»: без категории ищем по всем опубликованным профилям,
    специализация и грейд сужают поиск по отдельности.

    Фильтр — `evaluate_need`. Порядок снаружи: пять чисел потребности,
    бонус сдачи 0 или 1, затем `rank_key`. Согласие
    publication проверяется до подбора, не внутри matching (ТЗ §2.1
    «Настройки: приватность», §3.5 «152-ФЗ»).
    """
    rules = load_rules()
    if category_id:
        category_id = require_category(category_id)
        spec_of, grade_of = category_id.split(":")
        if (specialization and specialization != spec_of) or (grade and grade != grade_of):
            fail(422, "category_id")
        specialization, grade = spec_of, grade_of
    if specialization:
        _closed(specialization, rules["specializations"], "closed_list")
    if grade:
        _closed(grade, rules["grades"], "closed_list")
    for item in stack:
        _closed(item, rules["stack"], "closed_list")
    if team_role:
        _closed(team_role, rules["team_roles"], "closed_list")
    if work_format:
        _closed(work_format, rules["work_formats"], "closed_list")
    done_by_profile = dict(
        session.execute(
            select(PeriodicTask.profile_id, func.max(PeriodicTask.completed_at))
            .where(PeriodicTask.completed_at.is_not(None))
            .group_by(PeriodicTask.profile_id)
        ).all()
    )
    ranked: list[tuple[tuple[int, int, int, int, int, int, str], dict, int]] = []
    for profile, consent, assignment in published_rows(session):
        spec_of, _sep, grade_of = assignment.category_id.partition(":")
        if (specialization and spec_of != specialization) or (grade and grade_of != grade):
            continue
        # Одна и та же потребность, адресованная категории этого профиля:
        # ранжирование внутри категории и по всей базе считает один `evaluate_need`.
        need = _need(
            category_id=assignment.category_id,
            stack=stack,
            team_role=team_role,
            work_format=work_format,
            has_fsp=has_fsp,
            q=q,
        )
        fit, labels, achievements, rank = _profile_matches(
            session,
            profile,
            assignment,
            need,
            mention_achievements=bool(consent.show_achievements),
        )
        if not fit.matched:
            continue
        card = _card(profile, assignment, labels, achievements, rank, consent, reason=fit.reason)
        card.update(_test_view(session, assignment.test_attempt_id))
        done_at = done_by_profile.get(profile.id)
        card["task_line"] = task_line(done_at)
        overlap, role, fmt, fsp, tokens, rank_key = fit.sort_key
        key = (overlap, role, fmt, fsp, tokens, freshness_bonus(done_at), rank_key)
        ranked.append((key, card, consent.show_achievements))
    ranked.sort(key=lambda item: item[0], reverse=True)
    cards = []
    for index, (key, card, show_achievements) in enumerate(ranked):
        nxt = ranked[index + 1][0] if index + 1 < len(ranked) else None
        phrase = neighbor_phrase(key, nxt, mention_achievements=bool(show_achievements))
        if phrase:
            card["list_reason"] = f"{card['list_reason']} {phrase}".strip()
        cards.append(card)
    return cards


def _test_view(session: Session, attempt_id: int | None) -> dict:
    """Статус грейда на карточке работодателя (ТЗ §2.1 «обоснование попадания
    кандидата в подборку»; Q&A: неподтверждённый грейд показывать со статусом).

    Тест сообщает уверенность и положение в грейде: «подтверждён уверенно»,
    «подтверждён, у границы» или «не подтверждён тестом».
    """
    attempt = session.get(TestAttempt, attempt_id) if attempt_id else None
    if attempt is None:
        return {"grade_status": "не подтверждён", "test_confidence": None, "test_position": None}
    result = json.loads(attempt.result_json) if attempt.result_json else {}
    if attempt.outcome not in ("confirm", "suggest_higher"):
        status = "не подтверждён тестом"
    elif result.get("status") == "уверенно":
        status = "подтверждён уверенно"
    elif result.get("status"):
        status = "подтверждён, у границы"
    else:
        status = "подтверждён"
    confidence = result.get("confidence")
    return {
        "grade_status": status,
        "test_confidence": None if confidence is None else round(100 * confidence),
        "test_position": result.get("position"),
    }


def _card(
    profile: Profile,
    assignment: StoredCategory,
    labels: list[str],
    achievements: list[FspAchievement],
    rank: str,
    consent: Consent,
    *,
    reason: str,
) -> dict:
    listed = [
        {
            "discipline": row.discipline,
            "role_in_team": row.role_in_team,
            "place": row.place,
        }
        for row in achievements
    ]
    full_name = profile.full_name if consent.show_name else ""
    experience = profile.experience if consent.show_experience else ""
    shown = listed if consent.show_achievements else []
    fsp_rank = rank if consent.show_achievements else ""
    return {
        "profile_id": profile.id,
        "full_name": full_name,
        "industry": profile.industry,
        "category_id": assignment.category_id,
        "explanation": assignment.explanation,
        "list_reason": reason,
        "rank_key": assignment.rank_key,
        "test_score": assignment.test_score,
        "grade_status": "подтверждён" if assignment.test_score is not None else "не подтверждён",
        "stack": labels,
        "team_role": profile.team_role,
        "work_format": profile.work_format,
        "experience": experience,
        "fsp_rank": fsp_rank,
        "achievements": shown,
    }

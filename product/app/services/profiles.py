"""Анкета соискателя. Первая запись создаёт профиль, согласие и попытку опроса.

Грейд после первой записи этим запросом не меняется: его пишет только тест.

ТЗ §2 «опрос по отрасли и специализации, самостоятельный выбор грейда»; ТЗ §2.1 «Настройки… согласие»; ТЗ §3.5 «152-ФЗ».
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from product.app.db.models import Consent, Profile, SurveyAttempt, User
from product.app.log import log_event
from product.app.services.categories import latest_assignment, recompute
from product.app.services.clock import utc_now
from product.app.services.errors import hint as catalog_hint, fail
from product.app.services.fsp import achievements_for
from product.app.services.offers import list_candidate_offers
from product.app.services.tests import list_attempts
from product.matching.rules import load_rules, rule_version

_FORM_FIELDS = (
    "full_name",
    "industry",
    "stack",
    "specialization",
    "grade",
    "team_role",
    "work_format",
    "experience",
    "soft_skills",
    "external_id",
)
_EXTERNAL_ID_MAX = 64


def _as_labels(value: object, allowed: list[str], label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in allowed for item in value):
        fail(422, "closed_list", what=label, allowed=", ".join(allowed))
    return list(value)


def _one(value: object, allowed: list[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        fail(422, "closed_list", what=what, allowed=", ".join(allowed))
    return value


def _integrity_code(exc: IntegrityError) -> str:
    orig = str(getattr(exc, "orig", exc)).lower()
    if "uq_profiles_external" in orig or "external_id" in orig or "external" in orig:
        return "external_id"
    return "conflict"


def _visibility(payload: dict, key: str, current: int) -> int:
    if key not in payload or payload[key] is None:
        return current
    if not isinstance(payload[key], bool):
        fail(422, "visibility_flag")
    return 1 if payload[key] else 0


def profile_payload(db: Session, user: User, profile: Profile) -> dict:
    consent = db.scalar(select(Consent).where(Consent.profile_id == profile.id))
    assignment = latest_assignment(db, profile.id)
    publication = bool(consent.publication) if consent is not None else False
    show_name = True if consent is None else bool(consent.show_name)
    show_experience = True if consent is None else bool(consent.show_experience)
    show_achievements = True if consent is None else bool(consent.show_achievements)
    return {
        "profile_id": profile.id,
        "full_name": profile.full_name,
        "email": user.email,
        "industry": profile.industry,
        "specialization": profile.specialization,
        "grade": profile.grade,
        "stack": json.loads(profile.stack_json or "[]"),
        "team_role": profile.team_role,
        "work_format": profile.work_format,
        "experience": profile.experience,
        "soft_skills": json.loads(profile.soft_skills_json or "[]"),
        "external_id": profile.external_id,
        "publication": publication,
        "show_name": show_name,
        "show_experience": show_experience,
        "show_achievements": show_achievements,
        "category_id": assignment.category_id if assignment else None,
        "explanation": assignment.explanation if assignment else None,
        "field_sources": json.loads(profile.field_sources_json or "{}"),
    }


def list_surveys(db: Session, user: User) -> list[dict]:
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        return []
    rows = db.scalars(
        select(SurveyAttempt)
        .where(SurveyAttempt.profile_id == profile.id)
        .order_by(SurveyAttempt.id.desc())
    ).all()
    return [
        {
            "created_at": row.created_at,
            "specialization": row.specialization,
            "grade": row.grade,
            "rule_version": row.rule_version,
        }
        for row in rows
    ]


def cabinet_payload(db: Session, user: User) -> dict:
    """Один словарь для кабинета: профиль, опрос, тест, достижения."""
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    body = profile_payload(db, user, profile) if profile is not None else None
    history = list_attempts(db, user)
    surveys = list_surveys(db, user)
    has_offers = bool(list_candidate_offers(db, user)) if profile is not None else False
    test_done = any(row["status"] == "submitted" for row in history)
    publication = bool(user.consent_publication) if body is None else body["publication"]
    if body is None:
        show_name = show_experience = show_achievements = True
    else:
        show_name = body["show_name"]
        show_experience = body["show_experience"]
        show_achievements = body["show_achievements"]
    assignment = None if body is None or not body["category_id"] else body
    if profile is None:
        hint = catalog_hint("hint_no_profile")
    elif not test_done:
        hint = catalog_hint("hint_no_test", specialization=profile.specialization, grade=profile.grade)
    elif not publication:
        hint = catalog_hint("hint_no_publication")
    elif assignment is not None:
        hint = catalog_hint("hint_published", category_id=assignment["category_id"])
    else:
        hint = ""
    offers_hint = ""
    if profile is not None and not has_offers:
        offers_hint = catalog_hint("hint_no_invites_candidate")
    return {
        "profile": profile,
        "assignment": assignment,
        "fsp": achievements_for(db, None if profile is None else profile.external_id),
        "test_done": test_done,
        "has_offers": has_offers,
        "surveys": surveys,
        "history": history,
        "rules": load_rules(),
        "stack": [] if body is None else body["stack"],
        "soft": [] if body is None else body["soft_skills"],
        "field_sources": {} if body is None else body["field_sources"],
        "publication": publication,
        "show_name": show_name,
        "show_experience": show_experience,
        "show_achievements": show_achievements,
        "hint": hint,
        "offers_hint": offers_hint,
    }


def get_own_profile(db: Session, user: User) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        fail(404, "not_found", "not_found_profile")
    return profile


def save_profile(db: Session, user: User, payload: dict) -> dict:
    rules = load_rules()
    industry = _one(payload.get("industry"), rules["industries"], "отрасль")
    specialization = _one(payload.get("specialization"), rules["specializations"], "специализация")
    grade = _one(payload.get("grade"), rules["grades"], "грейд")
    team_role = _one(payload.get("team_role"), rules["team_roles"], "роль в компании")
    work_format = _one(payload.get("work_format"), rules["work_formats"], "формат работы")
    stack = _as_labels(payload.get("stack"), rules["stack"], "Стек")
    soft = _as_labels(payload.get("soft_skills"), rules["soft_skills"], "Софт-скиллы")
    full_name = payload.get("full_name")
    if not isinstance(full_name, str) or not full_name.strip():
        fail(422, "full_name")
    experience = payload.get("experience", "")
    if experience is None:
        experience = ""
    if not isinstance(experience, str):
        fail(422, "experience")
    external = payload.get("external_id")
    if external is None or external == "":
        external_id = None
    elif isinstance(external, str) and external.strip():
        external_id = external.strip()
        if len(external_id) > _EXTERNAL_ID_MAX:
            fail(422, "external_id")
    else:
        fail(422, "external_id")
    publication_given = "publication" in payload and payload["publication"] is not None
    flag = None
    if publication_given:
        if not isinstance(payload["publication"], bool):
            fail(422, "consent_required", "consent_publication")
        flag = 1 if payload["publication"] else 0

    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is not None and profile.grade != grade:
        fail(409, "grade_via_test")
    consent = None
    if profile is not None:
        consent = db.scalar(select(Consent).where(Consent.profile_id == profile.id))
    show_name = _visibility(payload, "show_name", 1 if consent is None else consent.show_name)
    show_experience = _visibility(payload, "show_experience", 1 if consent is None else consent.show_experience)
    show_achievements = _visibility(
        payload, "show_achievements", 1 if consent is None else consent.show_achievements
    )

    now = utc_now()
    stack_json = json.dumps(stack, ensure_ascii=False)
    soft_json = json.dumps(soft, ensure_ascii=False)
    if profile is None:
        sources = {key: "form" for key in _FORM_FIELDS}
        profile = Profile(
            user_id=user.id,
            full_name=full_name.strip(),
            industry=industry,
            stack_json=stack_json,
            specialization=specialization,
            grade=grade,
            grade_changed_at=now,
            team_role=team_role,
            work_format=work_format,
            experience=experience,
            soft_skills_json=soft_json,
            external_id=external_id,
            field_sources_json=json.dumps(sources, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        db.add(profile)
        db.flush()
        consent_flag = flag if flag is not None else (1 if user.consent_publication else 0)
        db.add(
            Consent(
                profile_id=profile.id,
                processing=consent_flag,
                publication=consent_flag,
                accepted_at=now,
                show_name=show_name,
                show_experience=show_experience,
                show_achievements=show_achievements,
            )
        )
    else:
        sources = json.loads(profile.field_sources_json or "{}")
        for key in _FORM_FIELDS:
            if key == "grade":
                continue
            sources[key] = "form"
        profile.full_name = full_name.strip()
        profile.industry = industry
        profile.stack_json = stack_json
        profile.specialization = specialization
        profile.team_role = team_role
        profile.work_format = work_format
        profile.experience = experience
        profile.soft_skills_json = soft_json
        profile.external_id = external_id
        profile.field_sources_json = json.dumps(sources, ensure_ascii=False)
        profile.updated_at = now
        if publication_given:
            if consent is None:
                consent = Consent(
                    profile_id=profile.id,
                    processing=flag,
                    publication=flag,
                    accepted_at=now,
                )
                db.add(consent)
            else:
                consent.processing = flag
                consent.publication = flag
                consent.accepted_at = now
        if consent is not None:
            consent.show_name = show_name
            consent.show_experience = show_experience
            consent.show_achievements = show_achievements

    db.add(
        SurveyAttempt(
            profile_id=profile.id,
            full_name=profile.full_name,
            industry=profile.industry,
            stack_json=profile.stack_json,
            specialization=profile.specialization,
            grade=profile.grade,
            team_role=profile.team_role,
            work_format=profile.work_format,
            experience=profile.experience,
            soft_skills_json=profile.soft_skills_json,
            external_id=profile.external_id,
            rule_version=rule_version(),
            created_at=now,
        )
    )
    try:
        db.flush()
        recompute(db, profile.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _integrity_code(exc) == "external_id":
            fail(409, "external_id", "external_id_taken")
        fail(409, "conflict")
    log_event("visibility_flag", f"{show_name} {show_experience} {show_achievements}")
    db.refresh(profile)
    return profile_payload(db, user, profile)

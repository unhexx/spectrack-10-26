"""Своя вакансия и отклик. Почта соискателя появляется только после accepted.

ТЗ §2.1 «Просмотр вакансий и отклик», «Регулярные задания» (одно короткое задание работодателя); ТЗ §2.2 п.2–3.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from product.app.db.models import Application, EmployerTask, User, Vacancy
from product.app.db.session import sqlite_int
from product.app.log import log_event
from product.app.services.categories import require_category
from product.app.services.clock import utc_now
from product.app.services.company import posted_company
from product.app.services.errors import fail
from product.app.services.fork import parse_fork
from product.app.services.profile_ref import candidate_email, own_profile_id
from product.matching.employer_task import answer_accepted

_VACANCY_FIELDS = {
    "title",
    "description",
    "category_id",
    "company_name",
    "contact_channel",
    "salary_min",
    "salary_max",
    "salary_currency",
    "task_prompt",
}
_APPLY_FIELDS = {"message", "task_answer"}


def _fail(status: int, code: str, key: str | None = None, **parts: object) -> None:
    log_event("vacancy_rejected", code)
    fail(status, code, key, **parts)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(422, code)
    return value.strip()


def _task_prompt(db: Session, vacancy_id: int) -> str:
    row = db.scalar(select(EmployerTask).where(EmployerTask.vacancy_id == vacancy_id))
    return "" if row is None else row.prompt


def vacancy_payload(db: Session, row: Vacancy) -> dict:
    return {
        "id": row.id,
        "category_id": row.category_id,
        "title": row.title,
        "description": row.description,
        "company_name": row.company_name,
        "contact_channel": row.contact_channel,
        "salary_min": row.salary_min,
        "salary_max": row.salary_max,
        "salary_currency": row.salary_currency,
        "task_prompt": _task_prompt(db, row.id),
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def application_payload(db: Session, row: Application, *, viewer: str) -> dict:
    vacancy = db.get(Vacancy, row.vacancy_id)
    body = {
        "id": row.id,
        "vacancy_id": row.vacancy_id,
        "profile_id": row.profile_id,
        "title": "" if vacancy is None else vacancy.title,
        "company_name": "" if vacancy is None else vacancy.company_name,
        "contact_channel": "" if vacancy is None else vacancy.contact_channel,
        "salary_min": 0 if vacancy is None else vacancy.salary_min,
        "salary_max": 0 if vacancy is None else vacancy.salary_max,
        "salary_currency": "RUB" if vacancy is None else vacancy.salary_currency,
        "message": row.message,
        "task_answer": row.task_answer or "",
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    # ТЗ §2.1 / §2.2 п.2: контакт откликнувшегося открывается работодателю после «принято».
    if viewer == "employer" and row.status == "accepted":
        body["candidate_email"] = candidate_email(db, row.profile_id)
    return body


def _own_vacancy(db: Session, user: User, vacancy_id: int) -> Vacancy:
    number = sqlite_int(vacancy_id)
    row = db.get(Vacancy, number) if number is not None else None
    if row is None or row.employer_id != user.id:
        fail(404, "not_found", "not_found_own_vacancy")
    return row


def _parsed_vacancy(db: Session, user: User, payload: dict) -> dict:
    """Общий разбор полей создания и правки. Нет ключа task_prompt — в словаре None."""
    extra = set(payload) - _VACANCY_FIELDS
    if extra:
        _fail(422, "extra_forbidden")
    company = posted_company(db, user, payload, _fail)
    title = _text(payload.get("title"), "vacancy_title")
    description = _text(payload.get("description"), "vacancy_text")
    category_id = payload.get("category_id")
    if not isinstance(category_id, str):
        _fail(422, "category_id")
    category_id = require_category(category_id)
    # ТЗ §2.2 п.1: вилка в рублях обязательна и в вакансии.
    salary_min, salary_max = parse_fork(payload.get("salary_min"), payload.get("salary_max"), _fail)
    if payload.get("salary_currency") != "RUB":
        _fail(422, "currency")
    prompt = None
    if "task_prompt" in payload:
        prompt = _text(payload.get("task_prompt"), "task_prompt")
        if len(prompt) > 2000:
            _fail(422, "task_prompt")
    return {
        "company": company,
        "title": title,
        "description": description,
        "category_id": category_id,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "task_prompt": prompt,
    }


def create_vacancy(db: Session, user: User, payload: dict) -> dict:
    fields = _parsed_vacancy(db, user, payload)
    company = fields["company"]
    now = utc_now()
    row = Vacancy(
        employer_id=user.id,
        category_id=fields["category_id"],
        title=fields["title"],
        description=fields["description"],
        company_name=company.name,
        contact_channel=company.contact,
        salary_min=fields["salary_min"],
        salary_max=fields["salary_max"],
        salary_currency="RUB",
        status="published",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    if fields["task_prompt"]:
        db.add(EmployerTask(vacancy_id=row.id, prompt=fields["task_prompt"], created_at=now))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _fail(409, "conflict")
    db.refresh(row)
    return vacancy_payload(db, row)


def get_own_vacancy(db: Session, user: User, vacancy_id: int) -> dict:
    return vacancy_payload(db, _own_vacancy(db, user, vacancy_id))


def update_vacancy(db: Session, user: User, vacancy_id: int, payload: dict) -> dict:
    row = _own_vacancy(db, user, vacancy_id)
    fields = _parsed_vacancy(db, user, payload)
    company = fields["company"]
    now = utc_now()
    row.category_id = fields["category_id"]
    row.title = fields["title"]
    row.description = fields["description"]
    row.company_name = company.name
    row.contact_channel = company.contact
    row.salary_min = fields["salary_min"]
    row.salary_max = fields["salary_max"]
    row.salary_currency = "RUB"
    row.updated_at = now
    prompt = fields["task_prompt"]
    task = db.scalar(select(EmployerTask).where(EmployerTask.vacancy_id == row.id))
    # Ключа нет — снять задание. Текст ответа и vacancy_id отклика не трогать.
    if prompt is None:
        if task is not None:
            db.delete(task)
    elif task is None:
        db.add(EmployerTask(vacancy_id=row.id, prompt=prompt, created_at=now))
    else:
        task.prompt = prompt
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _fail(409, "conflict")
    db.refresh(row)
    return vacancy_payload(db, row)


def list_employer_vacancies(db: Session, user: User) -> list[dict]:
    rows = db.scalars(
        select(Vacancy).where(Vacancy.employer_id == user.id).order_by(Vacancy.id.desc())
    ).all()
    return [vacancy_payload(db, row) for row in rows]


def list_published(db: Session) -> list[dict]:
    rows = db.scalars(select(Vacancy).where(Vacancy.status == "published").order_by(Vacancy.id.desc())).all()
    return [vacancy_payload(db, row) for row in rows]


def list_employer_applications(db: Session, user: User) -> list[dict]:
    rows = db.scalars(
        select(Application)
        .join(Vacancy, Application.vacancy_id == Vacancy.id)
        .where(Vacancy.employer_id == user.id)
        .order_by(Application.id.desc())
    ).all()
    return [application_payload(db, row, viewer="employer") for row in rows]


def list_candidate_applications(db: Session, user: User) -> list[dict]:
    profile_id = own_profile_id(db, user)
    if profile_id is None:
        return []
    rows = db.scalars(
        select(Application).where(Application.profile_id == profile_id).order_by(Application.id.desc())
    ).all()
    return [application_payload(db, row, viewer="candidate") for row in rows]


def _published(db: Session, vacancy_id: int) -> Vacancy:
    row = db.get(Vacancy, vacancy_id) if sqlite_int(vacancy_id) is not None else None
    if row is None or row.status != "published":
        fail(404, "not_found", "not_found_vacancy")
    return row


def apply_to_vacancy(db: Session, user: User, vacancy_id: int, payload: dict) -> dict:
    extra = set(payload) - _APPLY_FIELDS
    if extra:
        _fail(422, "extra_forbidden")
    vacancy = _published(db, vacancy_id)
    profile_id = own_profile_id(db, user)
    if profile_id is None:
        _fail(409, "profile_required")
    message = _text(payload.get("message"), "application_message")
    prompt = _task_prompt(db, vacancy.id)
    answer = ""
    if prompt:
        raw = payload.get("task_answer")
        if not isinstance(raw, str) or not answer_accepted(prompt, raw):
            _fail(422, "task_answer")
        answer = raw.strip()
    elif payload.get("task_answer"):
        _fail(422, "extra_forbidden")
    opened = db.scalar(
        select(Application).where(
            Application.vacancy_id == vacancy.id,
            Application.profile_id == profile_id,
            Application.status.in_(("sent", "seen")),
        )
    )
    if opened is not None:
        _fail(409, "application_open")
    now = utc_now()
    row = Application(
        vacancy_id=vacancy.id,
        profile_id=profile_id,
        message=message,
        task_answer=answer or None,
        status="sent",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _fail(409, "application_open")
    db.refresh(row)
    return application_payload(db, row, viewer="candidate")


def _employer_application(db: Session, user: User, application_id: int) -> Application:
    row = db.get(Application, application_id) if sqlite_int(application_id) is not None else None
    if row is None:
        fail(404, "not_found", "not_found_application")
    vacancy = db.get(Vacancy, row.vacancy_id)
    if vacancy is None or vacancy.employer_id != user.id:
        fail(404, "not_found", "not_found_application")
    return row


def _candidate_application(db: Session, user: User, application_id: int) -> Application:
    profile_id = own_profile_id(db, user)
    row = db.get(Application, application_id) if sqlite_int(application_id) is not None else None
    if row is None or profile_id is None or row.profile_id != profile_id:
        fail(404, "not_found", "not_found_application")
    return row


def open_application(db: Session, user: User, application_id: int) -> dict:
    row = _employer_application(db, user, application_id)
    if row.status == "sent":
        now = utc_now()
        result = db.execute(
            update(Application)
            .where(Application.id == row.id, Application.status == "sent")
            .values(status="seen", updated_at=now)
        )
        db.commit()
        db.refresh(row)
        if result.rowcount:
            log_event("application_status", "seen")
    return application_payload(db, row, viewer="employer")


def _close(db: Session, row: Application, status: str, *, viewer: str) -> dict:
    if row.status not in {"sent", "seen"}:
        _fail(409, "application_closed")
    now = utc_now()
    result = db.execute(
        update(Application)
        .where(Application.id == row.id, Application.status.in_(("sent", "seen")))
        .values(status=status, updated_at=now)
    )
    db.commit()
    db.refresh(row)
    if not result.rowcount:
        _fail(409, "application_closed")
    log_event("application_status", status)
    return application_payload(db, row, viewer=viewer)


def set_employer_application_status(db: Session, user: User, application_id: int, status: str) -> dict:
    row = _employer_application(db, user, application_id)
    return _close(db, row, status, viewer="employer")


def decline_application(db: Session, user: User, application_id: int) -> dict:
    row = _candidate_application(db, user, application_id)
    return _close(db, row, "declined", viewer="candidate")

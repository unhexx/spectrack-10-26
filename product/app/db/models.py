"""Схема стенда. Обязательный MVP собран в FSP-1; вакансия, отклик и задание — FSP-8.

Колонка offers.vacancy_id по-прежнему пустая у приглашения из подборки.
Внешний ключ на vacancies есть: опубликованная вакансия больше не «таблица потом».

ТЗ §2.2 «Данные и правила видимости»: вилка в рублях, статусы приглашений и откликов — ограничениями схемы.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('candidate', 'employer')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    email_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmation_code_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confirmation_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confirmation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consent_processing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consent_publication: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)



class LoginThrottle(Base):
    """Счётчик неудачных входов по адресу почты (ТЗ §3.5 «защита от
    несанкционированного доступа»). Ключ — нормализованная почта, в том числе
    незарегистрированная: так блокировка не выдаёт, есть ли учётная запись."""

    __tablename__ = "login_throttle"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[str | None] = mapped_column(String(40), nullable=True)

class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    contact: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("industry IN ('it')", name="ck_profiles_industry"),
        CheckConstraint(
            "specialization IN ('backend', 'frontend', 'mobile', 'data', 'security', 'devops')",
            name="ck_profiles_specialization",
        ),
        CheckConstraint(
            "grade IN ('junior', 'middle', 'senior', 'lead')",
            name="ck_profiles_grade",
        ),
        CheckConstraint(
            "work_format IN ('офис', 'удалённо', 'гибрид')",
            name="ck_profiles_work_format",
        ),
        CheckConstraint(
            "team_role IN ('разработчик', 'тимлид', 'аналитик')",
            name="ck_profiles_team_role",
        ),
        Index(
            "uq_profiles_external",
            "external_id",
            unique=True,
            sqlite_where=text("external_id IS NOT NULL AND external_id != ''"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str] = mapped_column(String(16), nullable=False)
    stack_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    specialization: Mapped[str] = mapped_column(String(32), nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    grade_changed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    team_role: Mapped[str] = mapped_column(String(32), nullable=False)
    work_format: Mapped[str] = mapped_column(String(16), nullable=False)
    experience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    soft_skills_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    field_sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), unique=True, nullable=False)
    processing: Mapped[int] = mapped_column(Integer, nullable=False)
    publication: Mapped[int] = mapped_column(Integer, nullable=False)
    show_name: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    show_experience: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    show_achievements: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    accepted_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SurveyAttempt(Base):
    __tablename__ = "survey_attempts"
    __table_args__ = (Index("ix_survey_profile", "profile_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str] = mapped_column(String(16), nullable=False)
    stack_json: Mapped[str] = mapped_column(Text, nullable=False)
    specialization: Mapped[str] = mapped_column(String(32), nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    team_role: Mapped[str] = mapped_column(String(32), nullable=False)
    work_format: Mapped[str] = mapped_column(String(16), nullable=False)
    experience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    soft_skills_json: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TestAttempt(Base):
    __tablename__ = "test_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'submitted')", name="ck_test_status"),
        Index(
            "uq_test_draft",
            "profile_id",
            unique=True,
            sqlite_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    specialization: Mapped[str] = mapped_column(String(32), nullable=False)
    declared_grade: Mapped[str] = mapped_column(String(16), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False)
    answers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proposed_grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Механика теста — product/matching/assessment.py; template_id хранит её
    # идентификатор (assessment.ENGINE). form_json — состав попытки:
    # вопросы, порядок вариантов, ключ (только на сервере, ТЗ §2 — устойчивость
    # к распространению заданий) и ответы. result_json — уровень, уверенность,
    # статус «уверенно / у границы», положение в грейде.
    form_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class CategoryAssignment(Base):
    __tablename__ = "category_assignments"
    __table_args__ = (
        Index("ix_category_lookup", "category_id", "rule_version"),
        Index("ix_category_profile", "profile_id", "rule_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    category_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    test_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_key: Mapped[str] = mapped_column(String(32), nullable=False)
    scored_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    test_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("test_attempts.id"), nullable=True)


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint("salary_currency = 'RUB'", name="ck_offers_currency"),
        CheckConstraint("salary_max > salary_min", name="ck_offers_range"),
        CheckConstraint("length(message) > 0", name="ck_offers_message"),
        CheckConstraint("length(company_name) > 0", name="ck_offers_company"),
        CheckConstraint("length(contact_channel) > 0", name="ck_offers_contact"),
        CheckConstraint(
            "status IN ('sent', 'seen', 'accepted', 'declined')",
            name="ck_offers_status",
        ),
        Index(
            "uq_offers_open",
            "employer_id",
            "profile_id",
            unique=True,
            sqlite_where=text("status IN ('sent', 'seen')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    category_id: Mapped[str] = mapped_column(String(64), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    salary_min: Mapped[int] = mapped_column(Integer, nullable=False)
    salary_max: Mapped[int] = mapped_column(Integer, nullable=False)
    salary_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    contact_channel: Mapped[str] = mapped_column(String(200), nullable=False)
    vacancy_id: Mapped[int | None] = mapped_column(ForeignKey("vacancies.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (
        CheckConstraint("salary_currency = 'RUB'", name="ck_vacancies_currency"),
        CheckConstraint("salary_max > salary_min", name="ck_vacancies_range"),
        CheckConstraint("length(title) > 0", name="ck_vacancies_title"),
        CheckConstraint("length(description) > 0", name="ck_vacancies_description"),
        CheckConstraint("length(company_name) > 0", name="ck_vacancies_company"),
        CheckConstraint("length(contact_channel) > 0", name="ck_vacancies_contact"),
        CheckConstraint("status IN ('published')", name="ck_vacancies_status"),
        Index("ix_vacancies_employer", "employer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    category_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_channel: Mapped[str] = mapped_column(String(200), nullable=False)
    salary_min: Mapped[int] = mapped_column(Integer, nullable=False)
    salary_max: Mapped[int] = mapped_column(Integer, nullable=False)
    salary_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EmployerTask(Base):
    __tablename__ = "employer_tasks"
    __table_args__ = (
        CheckConstraint("length(prompt) > 0", name="ck_employer_tasks_prompt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), unique=True, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("length(message) > 0", name="ck_applications_message"),
        CheckConstraint(
            "status IN ('sent', 'seen', 'accepted', 'declined')",
            name="ck_applications_status",
        ),
        Index("ix_applications_vacancy", "vacancy_id"),
        Index(
            "uq_applications_open",
            "vacancy_id",
            "profile_id",
            unique=True,
            sqlite_where=text("status IN ('sent', 'seen')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    task_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class FspParticipant(Base):
    __tablename__ = "fsp_participants"

    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    rank: Mapped[str] = mapped_column(String(64), nullable=False)


class FspAchievement(Base):
    __tablename__ = "fsp_achievements"
    __table_args__ = (
        Index("uq_fsp_result", "external_id", "competition_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(ForeignKey("fsp_participants.external_id"), nullable=False)
    competition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    competition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    discipline: Mapped[str] = mapped_column(String(200), nullable=False)
    role_in_team: Mapped[str] = mapped_column(String(120), nullable=False)
    place: Mapped[str] = mapped_column(String(64), nullable=False)
    held_on: Mapped[str] = mapped_column(String(40), nullable=False)


class PeriodicTask(Base):
    __tablename__ = "periodic_tasks"
    __table_args__ = (
        Index("ix_periodic_profile", "profile_id"),
        Index(
            "uq_periodic_open",
            "profile_id",
            unique=True,
            sqlite_where=text("completed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), nullable=False)
    offered_at: Mapped[str] = mapped_column(String(40), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

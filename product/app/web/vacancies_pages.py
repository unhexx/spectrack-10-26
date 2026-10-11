"""Страницы вакансии и отклика. Карточку компании этот модуль не пишет.

ТЗ §2.1 «Публикация вакансий», «Просмотр вакансий и отклик».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from product.app.api.deps import db_session
from product.app.services.company import get_company
from product.app.services.errors import ApiError, hint as catalog_hint
from product.app.services.vacancies import (
    apply_to_vacancy,
    create_vacancy,
    decline_application,
    get_own_vacancy,
    list_candidate_applications,
    list_employer_applications,
    list_employer_vacancies,
    list_published,
    open_application,
    set_employer_application_status,
    update_vacancy,
)
from product.app.web.forms import form_int
from product.app.web.guards import page_user, same_site_navigation
from product.app.web.render import assert_csrf, ensure_csrf, page_error, render

router = APIRouter()


def _employer_context(request: Request, db: Session, user, error: str = "") -> dict:
    company = None
    try:
        company = get_company(db, user)
    except ApiError:
        company = None
    name = "" if company is None else str(company.get("name") or "")
    contact = "" if company is None else str(company.get("contact") or "")
    return {
        "csrf": ensure_csrf(request),
        "error": error,
        "hint": "" if name.strip() and contact.strip() else catalog_hint("hint_vacancy_form"),
        "user": user,
        "company": company,
        "vacancies": list_employer_vacancies(db, user),
        "applications": list_employer_applications(db, user),
    }


def _candidate_context(request: Request, db: Session, user, error: str = "") -> dict:
    vacancies = list_published(db)
    applications = list_candidate_applications(db, user)
    return {
        "csrf": ensure_csrf(request),
        "error": error,
        "hint": "" if vacancies else catalog_hint("hint_no_vacancies"),
        "user": user,
        "vacancies": vacancies,
        "applications": applications,
    }


@router.get("/employer/vacancies")
def employer_vacancies(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    return render(request, "employer/vacancy.html", _employer_context(request, db, user))


def _form_payload(
    title: str,
    description: str,
    category_id: str,
    company_name: str,
    contact_channel: str,
    salary_min: str,
    salary_max: str,
    salary_currency: str,
    task_prompt: str,
) -> dict:
    payload = {
        "title": title,
        "description": description,
        "category_id": category_id,
        "company_name": company_name,
        "contact_channel": contact_channel,
        "salary_min": form_int(salary_min),
        "salary_max": form_int(salary_max),
        "salary_currency": salary_currency,
    }
    if task_prompt.strip():
        payload["task_prompt"] = task_prompt
    return payload


@router.post("/employer/vacancies")
def publish_page(
    request: Request,
    csrf: str = Form(""),
    title: str = Form(""),
    description: str = Form(""),
    category_id: str = Form(""),
    company_name: str = Form(""),
    contact_channel: str = Form(""),
    salary_min: str = Form(""),
    salary_max: str = Form(""),
    salary_currency: str = Form("RUB"),
    task_prompt: str = Form(""),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    payload = _form_payload(
        title,
        description,
        category_id,
        company_name,
        contact_channel,
        salary_min,
        salary_max,
        salary_currency,
        task_prompt,
    )
    try:
        assert_csrf(request, csrf)
        create_vacancy(db, user, payload)
    except ApiError as exc:
        return page_error(request, "employer/vacancy.html", _employer_context(request, db, user), exc)
    return RedirectResponse("/employer/vacancies", status_code=303)


@router.get("/employer/vacancies/{vacancy_id}")
def edit_vacancy_page(vacancy_id: int, request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    context = _employer_context(request, db, user)
    try:
        context["vacancy"] = get_own_vacancy(db, user, vacancy_id)
    except ApiError as exc:
        return page_error(request, "employer/vacancy.html", context, exc)
    return render(request, "employer/vacancy.html", context)


@router.post("/employer/vacancies/{vacancy_id}")
def update_vacancy_page(
    vacancy_id: int,
    request: Request,
    csrf: str = Form(""),
    title: str = Form(""),
    description: str = Form(""),
    category_id: str = Form(""),
    company_name: str = Form(""),
    contact_channel: str = Form(""),
    salary_min: str = Form(""),
    salary_max: str = Form(""),
    salary_currency: str = Form("RUB"),
    task_prompt: str = Form(""),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    payload = _form_payload(
        title,
        description,
        category_id,
        company_name,
        contact_channel,
        salary_min,
        salary_max,
        salary_currency,
        task_prompt,
    )
    try:
        assert_csrf(request, csrf)
        update_vacancy(db, user, vacancy_id, payload)
    except ApiError as exc:
        context = _employer_context(request, db, user)
        if exc.error != "csrf":
            try:
                context["vacancy"] = get_own_vacancy(db, user, vacancy_id)
            except ApiError:
                pass
        return page_error(request, "employer/vacancy.html", context, exc)
    return RedirectResponse(f"/employer/vacancies/{vacancy_id}", status_code=303)


@router.get("/employer/applications/{application_id}")
def open_page(application_id: int, request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    try:
        if same_site_navigation(request):
            open_application(db, user, application_id)
    except ApiError as exc:
        return page_error(request, "employer/vacancy.html", _employer_context(request, db, user), exc)
    return RedirectResponse("/employer/vacancies", status_code=303)


@router.post("/employer/applications/{application_id}/accept")
def accept_page(
    application_id: int,
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    try:
        assert_csrf(request, csrf)
        set_employer_application_status(db, user, application_id, "accepted")
    except ApiError as exc:
        return page_error(request, "employer/vacancy.html", _employer_context(request, db, user), exc)
    return RedirectResponse("/employer/vacancies", status_code=303)


@router.post("/employer/applications/{application_id}/decline")
def employer_decline_page(
    application_id: int,
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "employer")
    if denial is not None:
        return denial
    try:
        assert_csrf(request, csrf)
        set_employer_application_status(db, user, application_id, "declined")
    except ApiError as exc:
        return page_error(request, "employer/vacancy.html", _employer_context(request, db, user), exc)
    return RedirectResponse("/employer/vacancies", status_code=303)


@router.get("/candidate/vacancies")
def candidate_vacancies(request: Request, db: Session = Depends(db_session)):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    return render(request, "candidate/apply.html", _candidate_context(request, db, user))


@router.post("/candidate/vacancies/{vacancy_id}/apply")
def apply_page(
    vacancy_id: int,
    request: Request,
    csrf: str = Form(""),
    message: str = Form(""),
    task_answer: str = Form(""),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    payload: dict = {"message": message}
    if task_answer.strip():
        payload["task_answer"] = task_answer
    try:
        assert_csrf(request, csrf)
        apply_to_vacancy(db, user, vacancy_id, payload)
    except ApiError as exc:
        return page_error(request, "candidate/apply.html", _candidate_context(request, db, user), exc)
    return RedirectResponse("/candidate/vacancies", status_code=303)


@router.post("/candidate/applications/{application_id}/decline")
def withdraw_page(
    application_id: int,
    request: Request,
    csrf: str = Form(""),
    db: Session = Depends(db_session),
):
    user, denial = page_user(request, db, "candidate")
    if denial is not None:
        return denial
    try:
        assert_csrf(request, csrf)
        decline_application(db, user, application_id)
    except ApiError as exc:
        return page_error(request, "candidate/apply.html", _candidate_context(request, db, user), exc)
    return RedirectResponse("/candidate/vacancies", status_code=303)

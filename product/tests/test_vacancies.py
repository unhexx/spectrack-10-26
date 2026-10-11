"""Вакансия: вилка до вычета НДФЛ, отклик, почта только после принятия.

ТЗ §2.1 «Просмотр вакансий и отклик», «Публикация вакансий»; ТЗ §2.2 п.1–3.
"""

from __future__ import annotations

import sqlite3

from product.app.services.errors import explain
from product.tests.conftest import register_and_confirm

_PROFILE = {
    "full_name": "Иван Синтетиков",
    "industry": "it",
    "specialization": "backend",
    "grade": "middle",
    "stack": ["python"],
    "team_role": "разработчик",
    "work_format": "удалённо",
    "experience": "синтетический стаж",
    "soft_skills": ["коммуникация"],
    "publication": True,
}
_CARD = {
    "name": "Синтетика",
    "description": "Платежи",
    "direction": "backend",
    "contact": "telegram: @sintetika",
}


def _login(client, email: str) -> None:
    logged = client.post("/api/auth/login", json={"email": email, "password": "password-ok"})
    assert logged.status_code == 200, logged.text


def _candidate(client, email: str) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json=_PROFILE)
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _employer(client, email: str = "boss-vacancy@example.com") -> None:
    register_and_confirm(client, email=email, role="employer")
    saved = client.put("/api/employer/company", json=_CARD)
    assert saved.status_code == 200, saved.text


def _vacancy(**extra) -> dict:
    body = {
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
    body.update(extra)
    return body


def _csrf(html: str) -> str:
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def test_vacancy_rejects_bad_range_and_hides_email_until_accept(client):
    register_and_confirm(client, email="bare-boss@example.com", role="employer")
    missing = client.post("/api/employer/vacancies", json=_vacancy())
    assert missing.status_code == 422
    assert missing.json()["error"] == "company_name"
    _employer(client)
    assert client.post("/api/employer/vacancies", json=_vacancy(salary_max=180000)).json()["error"] == "salary_range"
    assert client.post("/api/employer/vacancies", json=_vacancy(salary_max=10**30)).json()["error"] == "salary_range"
    assert client.post("/api/employer/vacancies", json=_vacancy(salary_min="180000")).json()["error"] == "salary_range"
    assert client.post("/api/employer/vacancies", json=_vacancy(salary_currency="USD")).json()["error"] == "currency"
    assert client.post("/api/employer/vacancies", json=_vacancy(category_id="it:middle")).json()["error"] == "category_id"
    assert client.post("/api/employer/vacancies", json=_vacancy(title="  ")).json()["error"] == "vacancy_title"
    odd = client.post("/api/employer/vacancies", json={**_vacancy(), "vacancy_id": 5})
    assert odd.status_code == 422
    assert odd.json()["error"] == "extra_forbidden"
    created = client.post("/api/employer/vacancies", json=_vacancy())
    assert created.status_code == 201, created.text
    ghost = client.post(f"/api/employer/applications/{10**30}/open")
    assert ghost.status_code == 404
    assert ghost.json()["error"] == "not_found"
    vacancy_id = created.json()["id"]
    assert created.json()["task_prompt"]
    assert "candidate_email" not in created.json()
    _candidate(client, "seeker-vacancy@example.com")
    denied = client.post("/api/employer/vacancies", json=_vacancy())
    assert denied.status_code == 403
    missing_vacancy = client.post(
        f"/api/vacancies/{10**30}/apply",
        json={"message": "Готов обсудить платежи"},
    )
    assert missing_vacancy.status_code == 404
    assert missing_vacancy.json()["error"] == "not_found"
    listed = client.get("/api/vacancies")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == vacancy_id
    assert "seeker-vacancy@example.com" not in listed.text
    empty = client.post(f"/api/vacancies/{vacancy_id}/apply", json={"message": "Готов"})
    assert empty.status_code == 422
    assert empty.json()["error"] == "task_answer"
    applied = client.post(
        f"/api/vacancies/{vacancy_id}/apply",
        json={"message": "Готов обсудить платежи", "task_answer": "Idempotency-Key"},
    )
    assert applied.status_code == 201, applied.text
    assert "candidate_email" not in applied.json()
    application_id = applied.json()["id"]
    again = client.post(
        f"/api/vacancies/{vacancy_id}/apply",
        json={"message": "Ещё раз", "task_answer": "Idempotency-Key"},
    )
    assert again.status_code == 409
    assert again.json()["error"] == "application_open"
    _login(client, "boss-vacancy@example.com")
    hidden = client.get("/api/employer/applications")
    assert hidden.status_code == 200
    assert "candidate_email" not in hidden.json()[0]
    assert "seeker-vacancy@example.com" not in hidden.text
    opened = client.post(f"/api/employer/applications/{application_id}/open")
    assert opened.status_code == 200
    assert opened.json()["status"] == "seen"
    assert "candidate_email" not in opened.json()
    accepted = client.post(f"/api/employer/applications/{application_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["candidate_email"] == "seeker-vacancy@example.com"
    assert accepted.json()["contact_channel"] == "telegram: @sintetika"
    closed = client.post(f"/api/employer/applications/{application_id}/decline")
    assert closed.status_code == 409
    assert closed.json()["error"] == "application_closed"
    _candidate(client, "other-vacancy@example.com")
    foreign = client.post(f"/api/candidate/applications/{application_id}/decline")
    assert foreign.status_code == 404
    assert foreign.json()["error"] == "not_found"


def test_apply_without_profile_and_without_task(client):
    _employer(client, email="plain-boss@example.com")
    created = client.post("/api/employer/vacancies", json=_vacancy(task_prompt=None))
    assert created.status_code == 422
    body = _vacancy()
    del body["task_prompt"]
    created = client.post("/api/employer/vacancies", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["task_prompt"] == ""
    register_and_confirm(client, email="no-profile@example.com", role="candidate")
    missing = client.post(
        f"/api/vacancies/{created.json()['id']}/apply",
        json={"message": "Без анкеты"},
    )
    assert missing.status_code == 409
    assert missing.json()["error"] == "profile_required"
    _candidate(client, "with-profile@example.com")
    applied = client.post(
        f"/api/vacancies/{created.json()['id']}/apply",
        json={"message": "Без задания"},
    )
    assert applied.status_code == 201, applied.text
    withdrawn = client.post(f"/api/candidate/applications/{applied.json()['id']}/decline")
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "declined"
    assert "candidate_email" not in withdrawn.json()


def test_pages_show_gross_fork_and_email_only_after_accept(client):
    register_and_confirm(client, email="page-boss-vac@example.com", role="employer")
    bare = client.get("/employer/vacancies")
    assert bare.status_code == 200
    assert "disabled" in bare.text
    saved = client.put("/api/employer/company", json=_CARD)
    assert saved.status_code == 200, saved.text
    form = client.get("/employer/vacancies")
    assert "Вилка от, руб. (до вычета НДФЛ)" in form.text
    assert "Вилка до, руб. (до вычета НДФЛ)" in form.text
    assert "Суммы до вычета НДФЛ." in form.text
    assert "нетто" not in form.text
    assert "на руки" not in form.text
    sent = client.post(
        "/employer/vacancies",
        data={
            "csrf": _csrf(form.text),
            "title": "Backend middle",
            "description": "Платежи",
            "category_id": "backend:middle",
            "company_name": "Синтетика",
            "contact_channel": "telegram: @sintetika",
            "salary_min": "180000",
            "salary_max": "250000",
            "salary_currency": "RUB",
            "task_prompt": "Один заголовок",
        },
        follow_redirects=False,
    )
    assert sent.status_code == 303
    _candidate(client, "page-seeker-vac@example.com")
    page = client.get("/candidate/vacancies")
    assert "до вычета НДФЛ" in page.text
    assert "нетто" not in page.text
    assert "на руки" not in page.text
    assert "page-seeker-vac@example.com" not in page.text
    assert "Один заголовок" in page.text
    vacancy_id = page.text.split("/candidate/vacancies/")[1].split("/")[0]
    applied = client.post(
        f"/candidate/vacancies/{vacancy_id}/apply",
        data={
            "csrf": _csrf(page.text),
            "message": "Готов",
            "task_answer": "Idempotency-Key",
        },
        follow_redirects=False,
    )
    assert applied.status_code == 303
    mine = client.get("/candidate/vacancies")
    assert "статус sent" in mine.text
    _login(client, "page-boss-vac@example.com")
    board = client.get("/employer/vacancies")
    assert "page-seeker-vac@example.com" not in board.text
    assert "до вычета НДФЛ" in board.text
    application_id = board.text.split("/employer/applications/")[1].split('"')[0]
    opened = client.get(f"/employer/applications/{application_id}", follow_redirects=False)
    assert opened.status_code == 303
    seen = client.get("/employer/vacancies")
    assert "статус seen" in seen.text
    assert "page-seeker-vac@example.com" not in seen.text
    accepted = client.post(
        f"/employer/applications/{application_id}/accept",
        data={"csrf": _csrf(seen.text)},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    after = client.get("/employer/vacancies")
    assert "Почта соискателя: page-seeker-vac@example.com" in after.text


def test_vacancy_form_double_minus_is_rejected_not_crashed(client):
    _employer(client, email="minus-vac-boss@example.com")
    form = client.get("/employer/vacancies")
    bad = client.post(
        "/employer/vacancies",
        data={
            "csrf": _csrf(form.text),
            "title": "Backend middle",
            "description": "Платежи",
            "category_id": "backend:middle",
            "company_name": "Синтетика",
            "contact_channel": "telegram: @sintetika",
            "salary_min": "180000",
            "salary_max": "--250000",
            "salary_currency": "RUB",
        },
    )
    assert bad.status_code == 422
    assert "целыми числами" in bad.text


def _stamp(client, vacancy_id: int, stamp: str) -> None:
    with sqlite3.connect(client.db_path) as db:
        db.execute("update vacancies set updated_at = ? where id = ?", (stamp, vacancy_id))


def _stored_application(client, application_id: int):
    with sqlite3.connect(client.db_path) as db:
        return db.execute(
            "select vacancy_id, task_answer from applications where id = ?",
            (application_id,),
        ).fetchone()


def _stored_task(client, vacancy_id: int):
    with sqlite3.connect(client.db_path) as db:
        return db.execute(
            "select prompt from employer_tasks where vacancy_id = ?",
            (vacancy_id,),
        ).fetchone()


def test_update_own_vacancy_keeps_id_applications_and_status(client):
    _employer(client, email="edit-boss@example.com")
    created = client.post("/api/employer/vacancies", json=_vacancy())
    assert created.status_code == 201, created.text
    vacancy_id = created.json()["id"]
    created_at = created.json()["created_at"]
    _candidate(client, "edit-seeker@example.com")
    applied = client.post(
        f"/api/vacancies/{vacancy_id}/apply",
        json={"message": "Готов обсудить платежи", "task_answer": "Idempotency-Key"},
    )
    assert applied.status_code == 201, applied.text
    application_id = applied.json()["id"]
    _login(client, "edit-boss@example.com")
    old = "2000-01-01T00:00:00Z"
    _stamp(client, vacancy_id, old)
    changed = _vacancy(
        title="Backend middle правка",
        description="Платежи, дневная смена",
        salary_max=260000,
        task_prompt="Назовите второй заголовок.",
    )
    updated = client.post(f"/api/employer/vacancies/{vacancy_id}", json=changed)
    assert updated.status_code == 200, updated.text
    saved = updated.json()
    assert saved["id"] == vacancy_id
    assert saved["title"] == "Backend middle правка"
    assert saved["description"] == "Платежи, дневная смена"
    assert saved["salary_max"] == 260000
    assert saved["task_prompt"] == "Назовите второй заголовок."
    assert saved["status"] == "published"
    assert saved["created_at"] == created_at
    assert saved["updated_at"] != old
    assert _stored_task(client, vacancy_id) == ("Назовите второй заголовок.",)
    assert _stored_application(client, application_id) == (vacancy_id, "Idempotency-Key")
    with sqlite3.connect(client.db_path) as db:
        stored = db.execute(
            "select status, created_at from vacancies where id = ?",
            (vacancy_id,),
        ).fetchone()
        count = db.execute("select count(*) from vacancies").fetchone()[0]
    assert stored == ("published", created_at)
    assert count == 1
    _stamp(client, vacancy_id, old)
    same = client.post(f"/api/employer/vacancies/{vacancy_id}", json=changed)
    assert same.status_code == 200, same.text
    assert same.json()["updated_at"] != old
    assert same.json()["created_at"] == created_at
    assert same.json()["title"] == "Backend middle правка"
    assert same.json()["status"] == "published"
    cleared = dict(changed)
    del cleared["task_prompt"]
    gone = client.post(f"/api/employer/vacancies/{vacancy_id}", json=cleared)
    assert gone.status_code == 200, gone.text
    assert gone.json()["id"] == vacancy_id
    assert gone.json()["task_prompt"] == ""
    assert gone.json()["status"] == "published"
    assert _stored_task(client, vacancy_id) is None
    assert _stored_application(client, application_id) == (vacancy_id, "Idempotency-Key")
    restored = client.post(
        f"/api/employer/vacancies/{vacancy_id}",
        json={**cleared, "task_prompt": "Назовите третий заголовок."},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["task_prompt"] == "Назовите третий заголовок."
    assert restored.json()["status"] == "published"
    assert _stored_task(client, vacancy_id) == ("Назовите третий заголовок.",)
    assert _stored_application(client, application_id) == (vacancy_id, "Idempotency-Key")
    extra = client.post(f"/api/employer/vacancies/{vacancy_id}", json={**cleared, "note": "лишнее"})
    assert extra.status_code == 422
    assert extra.json()["error"] == "extra_forbidden"
    status_field = client.post(
        f"/api/employer/vacancies/{vacancy_id}",
        json={**cleared, "status": "draft"},
    )
    assert status_field.status_code == 422
    assert status_field.json()["error"] == "extra_forbidden"
    with sqlite3.connect(client.db_path) as db:
        status = db.execute("select status from vacancies where id = ?", (vacancy_id,)).fetchone()[0]
    assert status == "published"
    own_missing = explain("not_found_own_vacancy")
    assert own_missing != explain("not_found_vacancy")
    assert "откликн" not in own_missing.casefold()
    _employer(client, email="edit-other@example.com")
    foreign = client.post(f"/api/employer/vacancies/{vacancy_id}", json=cleared)
    assert foreign.status_code == 404
    assert foreign.json()["error"] == "not_found"
    assert foreign.json()["message"] == own_missing
    missing = client.post("/api/employer/vacancies/999999", json=cleared)
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    assert missing.json()["message"] == own_missing
    huge = client.post(f"/api/employer/vacancies/{10**30}", json=cleared)
    assert huge.status_code == 404
    assert huge.json()["error"] == "not_found"
    assert huge.json()["message"] == own_missing


def test_edit_page_fills_the_same_form_and_saves(client):
    _employer(client, email="page-edit-boss@example.com")
    empty = client.get("/employer/vacancies")
    assert 'method="post" action="/employer/vacancies"' in empty.text
    assert ">Опубликовать вакансию<" in empty.text
    assert 'id="title" name="title" type="text" required>' in empty.text
    created = client.post("/api/employer/vacancies", json=_vacancy())
    assert created.status_code == 201, created.text
    vacancy_id = created.json()["id"]
    board = client.get("/employer/vacancies")
    assert 'method="post" action="/employer/vacancies"' in board.text
    assert 'method="post" action="/employer/vacancies/' not in board.text
    assert ">Опубликовать вакансию<" in board.text
    assert f'href="/employer/vacancies/{vacancy_id}"' in board.text
    assert "Править" in board.text
    edit = client.get(f"/employer/vacancies/{vacancy_id}")
    assert edit.status_code == 200
    assert f'method="post" action="/employer/vacancies/{vacancy_id}"' in edit.text
    assert ">Сохранить<" in edit.text
    assert "Опубликовать вакансию" not in edit.text
    assert 'value="Backend middle"' in edit.text
    assert "Платежи, без дежурств" in edit.text
    saved = client.post(
        f"/employer/vacancies/{vacancy_id}",
        data={
            "csrf": _csrf(edit.text),
            "title": "Backend middle правка",
            "description": "Платежи, дневная смена",
            "category_id": "backend:middle",
            "company_name": "Синтетика",
            "contact_channel": "telegram: @sintetika",
            "salary_min": "180000",
            "salary_max": "260000",
            "salary_currency": "RUB",
            "task_prompt": "Один заголовок",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"].endswith(f"/employer/vacancies/{vacancy_id}")
    again = client.get(f"/employer/vacancies/{vacancy_id}")
    assert again.status_code == 200
    assert 'value="Backend middle правка"' in again.text
    assert ">Сохранить<" in again.text
    listed = client.get("/api/employer/vacancies")
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == vacancy_id
    assert listed.json()[0]["title"] == "Backend middle правка"
    assert listed.json()[0]["status"] == "published"
    _employer(client, email="page-edit-other@example.com")
    foreign = client.get(f"/employer/vacancies/{vacancy_id}")
    assert foreign.status_code == 404
    assert ">Сохранить<" not in foreign.text
    missing = client.get(f"/employer/vacancies/{10**30}")
    assert missing.status_code == 404


def test_edit_page_posts_live_company_after_rename(client):
    _employer(client, email="rename-boss@example.com")
    created = client.post("/api/employer/vacancies", json=_vacancy())
    assert created.status_code == 201, created.text
    vacancy_id = created.json()["id"]
    renamed = {
        "name": "Новая синтетика",
        "description": "Платежи",
        "direction": "backend",
        "contact": "telegram: @nova",
    }
    saved_card = client.put("/api/employer/company", json=renamed)
    assert saved_card.status_code == 200, saved_card.text
    edit = client.get(f"/employer/vacancies/{vacancy_id}")
    assert edit.status_code == 200
    assert 'id="company_name" name="company_name" type="text" required readonly value="Новая синтетика"' in edit.text
    assert 'id="contact_channel" name="contact_channel" type="text" required readonly value="telegram: @nova"' in edit.text
    assert 'value="Backend middle"' in edit.text
    assert "Платежи, без дежурств" in edit.text
    saved = client.post(
        f"/employer/vacancies/{vacancy_id}",
        data={
            "csrf": _csrf(edit.text),
            "title": "Backend middle",
            "description": "Платежи, без дежурств",
            "category_id": "backend:middle",
            "company_name": "Новая синтетика",
            "contact_channel": "telegram: @nova",
            "salary_min": "180000",
            "salary_max": "250000",
            "salary_currency": "RUB",
            "task_prompt": "Назовите один заголовок идемпотентного POST.",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"].endswith(f"/employer/vacancies/{vacancy_id}")
    listed = client.get("/api/employer/vacancies")
    assert listed.status_code == 200
    row = listed.json()[0]
    assert row["id"] == vacancy_id
    assert row["company_name"] == "Новая синтетика"
    assert row["contact_channel"] == "telegram: @nova"
    assert row["title"] == "Backend middle"
    assert row["status"] == "published"

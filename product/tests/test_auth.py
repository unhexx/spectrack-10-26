"""Почта, код, роли и карточка компании."""

from __future__ import annotations

import sqlite3

from product.tests.conftest import register_and_confirm

_PROFILE = {
    "full_name": "Иван Синтетиков",
    "industry": "it",
    "specialization": "backend",
    "grade": "middle",
    "stack": ["python", "sql"],
    "team_role": "разработчик",
    "work_format": "удалённо",
    "experience": "3 года",
    "soft_skills": ["коммуникация"],
    "external_id": "",
    "publication": True,
}


def _csrf(html: str) -> str:
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]


def test_register_requires_consent_and_rejects_bad_input(client):
    missing = client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "password-ok", "role": "candidate"},
    )
    assert missing.status_code == 422
    assert missing.json()["error"] == "consent_required"

    short = client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "short", "role": "candidate", "consent": True},
    )
    assert short.status_code == 422
    assert short.json()["error"] == "password_length"

    role = client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "password-ok", "role": "admin", "consent": True},
    )
    assert role.status_code == 422
    assert role.json()["error"] == "role"

    broken = client.post(
        "/api/auth/register",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    assert broken.status_code == 400
    assert broken.json()["error"] == "json"


def test_code_is_hash_and_login_needs_confirmation(client):
    created = client.post(
        "/api/auth/register",
        json={
            "email": "Seeker@Example.com",
            "password": "password-ok",
            "role": "candidate",
            "consent": True,
        },
    )
    assert created.status_code == 201
    code = created.json()["code"]
    assert len(code) == 6 and code.isdigit()
    assert "product_session" in created.headers.get("set-cookie", "")

    early = client.post(
        "/api/auth/login",
        json={"email": "seeker@example.com", "password": "password-ok"},
    )
    assert early.status_code == 403
    assert early.json()["error"] == "email_unverified"

    wrong = client.post(
        "/api/auth/confirm",
        json={"email": "seeker@example.com", "code": "000000" if code != "000000" else "000001"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "code_invalid"

    ok = client.post("/api/auth/confirm", json={"email": "seeker@example.com", "code": code})
    assert ok.status_code == 200
    again = client.post("/api/auth/confirm", json={"email": "seeker@example.com", "code": code})
    assert again.status_code == 409
    assert again.json()["error"] == "already_verified"

    duplicate = client.post(
        "/api/auth/register",
        json={"email": "seeker@example.com", "password": "password-ok", "role": "employer"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "email_taken"

    logged = client.post(
        "/api/auth/login",
        json={"email": "seeker@example.com", "password": "password-ok"},
    )
    assert logged.status_code == 200
    assert logged.json()["role"] == "candidate"
    bad = client.post(
        "/api/auth/login",
        json={"email": "seeker@example.com", "password": "password-no"},
    )
    assert bad.status_code == 401
    assert bad.json()["error"] == "credentials"

    with sqlite3.connect(client.db_path) as db:
        row = db.execute(
            "select password_hash, confirmation_code_hash, email_verified, consent_processing, consent_publication from users"
        ).fetchone()
    assert row[0] != "password-ok"
    assert row[0].startswith("$argon2id$")
    assert row[1] is None
    assert row[2] == 1
    assert row[3] == 1 and row[4] == 1

    out = client.post("/api/auth/logout")
    assert out.status_code == 204
    closed = client.get("/api/candidate/profile")
    assert closed.status_code == 401
    assert closed.json()["error"] == "unauthorized"


def test_code_locks_then_resend_and_expiry(client):
    created = client.post(
        "/api/auth/register",
        json={"email": "lock@example.com", "password": "password-ok", "role": "candidate", "consent": False},
    )
    code = created.json()["code"]
    for _ in range(4):
        bad = client.post("/api/auth/confirm", json={"email": "lock@example.com", "code": "111111"})
        assert bad.status_code == 400
        assert bad.json()["error"] == "code_invalid"
    locked = client.post("/api/auth/confirm", json={"email": "lock@example.com", "code": "111111"})
    assert locked.status_code == 400
    assert locked.json()["error"] == "code_locked"
    stale = client.post("/api/auth/confirm", json={"email": "lock@example.com", "code": code})
    assert stale.status_code == 400
    assert stale.json()["error"] == "code_locked"

    resent = client.post("/api/auth/confirm/resend", json={"email": "lock@example.com"})
    assert resent.status_code == 200
    fresh = resent.json()["code"]
    assert fresh != code
    ok = client.post("/api/auth/confirm", json={"email": "lock@example.com", "code": fresh})
    assert ok.status_code == 200

    other = client.post(
        "/api/auth/register",
        json={"email": "late@example.com", "password": "password-ok", "role": "employer"},
    )
    with sqlite3.connect(client.db_path) as db:
        db.execute(
            "update users set confirmation_expires_at = '2000-01-01T00:00:00Z' where email = ?",
            ("late@example.com",),
        )
        db.commit()
    expired = client.post(
        "/api/auth/confirm",
        json={"email": "late@example.com", "code": other.json()["code"]},
    )
    assert expired.status_code == 400
    assert expired.json()["error"] == "code_expired"
    unknown = client.post("/api/auth/confirm/resend", json={"email": "missing@example.com"})
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "email"


def test_roles_profile_and_company(client):
    register_and_confirm(client, email="seeker@example.com", role="candidate", consent=True)
    empty = client.get("/api/candidate/profile")
    assert empty.status_code == 404
    assert empty.json()["error"] == "not_found"
    saved = client.put("/api/candidate/profile", json=_PROFILE)
    assert saved.status_code == 200
    body = saved.json()
    assert body["industry"] == "it"
    assert body["category_id"] == "backend:middle"
    assert "/" not in body["category_id"]
    assert body["explanation"].startswith("Опрос:")
    assert body["email"] == "seeker@example.com"
    assert "candidate_email" not in body
    assert body["field_sources"]["grade"] == "form"
    renamed = client.put("/api/candidate/profile", json={**_PROFILE, "full_name": "Иван Обновлённый"})
    assert renamed.status_code == 200
    assert renamed.json()["full_name"] == "Иван Обновлённый"
    hidden = client.put("/api/candidate/profile", json={**_PROFILE, "publication": False})
    assert hidden.status_code == 200
    assert hidden.json()["publication"] is False
    omitted = {key: value for key, value in _PROFILE.items() if key != "publication"}
    kept = client.put("/api/candidate/profile", json=omitted)
    assert kept.status_code == 200
    assert kept.json()["publication"] is False
    blocked = client.put("/api/candidate/profile", json={**_PROFILE, "grade": "senior"})
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "grade_via_test"
    assert client.get("/api/candidate/profile").json()["grade"] == "middle"
    alien = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "specialization": "design"},
    )
    assert alien.status_code == 422
    assert alien.json()["error"] == "closed_list"
    achievements = client.get("/api/candidate/achievements")
    assert achievements.status_code == 200
    assert achievements.json() == {"display_name": "", "rank": "", "results": []}
    foreign = client.get("/api/employer/company")
    assert foreign.status_code == 403
    assert foreign.json()["error"] == "forbidden"

    register_and_confirm(client, email="boss@example.com", role="employer")
    denied = client.get("/api/candidate/profile")
    assert denied.status_code == 403
    missing = client.get("/api/employer/company")
    assert missing.status_code == 404
    assert missing.json()["error"] == "company_missing"
    card = client.put(
        "/api/employer/company",
        json={
            "name": "Синтетика",
            "description": "Платежи",
            "direction": "backend",
            "contact": "telegram: @sintetika",
        },
    )
    assert card.status_code == 200
    assert card.json()["contact"] == "telegram: @sintetika"
    extra = client.put(
        "/api/employer/company",
        json={
            "name": "Синтетика",
            "description": "",
            "direction": "",
            "contact": "telegram: @sintetika",
            "inn": "7700000000",
        },
    )
    assert extra.status_code == 422
    assert extra.json()["error"] == "extra_forbidden"


def test_long_external_id_is_not_stored(client):
    register_and_confirm(client)
    too_long = "b" * 65
    rejected = client.put("/api/candidate/profile", json={**_PROFILE, "external_id": too_long})
    assert rejected.status_code == 422
    assert rejected.json()["error"] == "external_id"
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("select count(*) from profiles").fetchone()[0] == 0
        assert db.execute("select count(*) from survey_attempts").fetchone()[0] == 0
    exact = "a" * 64
    saved = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "external_id": " " + exact + " "},
    )
    assert saved.status_code == 200
    assert saved.json()["external_id"] == exact
    again = client.put("/api/candidate/profile", json={**_PROFILE, "external_id": too_long})
    assert again.status_code == 422
    assert again.json()["error"] == "external_id"
    assert client.get("/api/candidate/profile").json()["external_id"] == exact
    with sqlite3.connect(client.db_path) as db:
        stored = db.execute("select external_id from profiles").fetchall()
        attempts = db.execute("select external_id from survey_attempts").fetchall()
    assert stored == [(exact,)]
    assert attempts == [(exact,)]
    assert too_long[:64] not in {row[0] for row in stored + attempts}


def test_pages_keep_roles_and_show_code_on_the_page(client):
    page = client.get("/auth/register")
    token = _csrf(page.text)
    created = client.post(
        "/auth/register",
        data={
            "email": "page@example.com",
            "password": "password-ok",
            "role": "candidate",
            "consent": "1",
            "csrf": token,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/auth/confirm"
    assert "code" not in created.headers["location"]
    confirm = client.get("/auth/confirm")
    assert confirm.status_code == 200
    assert 'id="stand-code"' in confirm.text
    code = confirm.text.split('id="stand-code">', 1)[1].split("<", 1)[0]
    assert code.isdigit()
    token = _csrf(confirm.text)
    done = client.post(
        "/auth/confirm",
        data={"email": "page@example.com", "code": code, "csrf": token},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"] == "/auth/login"
    login_page = client.get("/auth/login")
    entered = client.post(
        "/auth/login",
        data={
            "email": "page@example.com",
            "password": "password-ok",
            "csrf": _csrf(login_page.text),
        },
        follow_redirects=False,
    )
    assert entered.status_code == 303
    assert entered.headers["location"] == "/candidate"
    cabinet = client.get("/candidate")
    assert cabinet.status_code == 200
    assert "Тест ещё не сдан" in cabinet.text
    assert "Приглашений нет" in cabinet.text
    assert "Достижений ФСП нет" in cabinet.text
    assert "Из резюме" in cabinet.text
    boss = client.get("/employer")
    assert boss.status_code == 403
    naked = client.post(
        "/auth/register",
        data={"email": "x@example.com", "password": "password-ok", "role": "candidate", "consent": "1"},
    )
    assert naked.status_code == 403
    assert naked.json()["error"] == "csrf"


def test_login_locks_after_five_wrong_passwords(client):
    """ТЗ §3.5 «защита от несанкционированного доступа»: подбор пароля упирается
    в блокировку на 15 минут; верный пароль во время блокировки не пускает."""
    register_and_confirm(client, email="brute@example.com", password="password-ok")
    for _ in range(4):
        wrong = client.post("/api/auth/login", json={"email": "brute@example.com", "password": "nope-nope"})
        assert wrong.status_code == 401
    fifth = client.post("/api/auth/login", json={"email": "Brute@example.com ", "password": "nope-nope"})
    assert fifth.status_code == 401
    locked = client.post("/api/auth/login", json={"email": "brute@example.com", "password": "password-ok"})
    assert locked.status_code == 429
    assert locked.json()["error"] == "login_locked"
    # Незарегистрированный адрес блокируется так же — ответ не выдаёт, есть ли учётка.
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope-nope"})
    ghost = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope-nope"})
    assert ghost.status_code == 429
    with sqlite3.connect(client.db_path) as db:
        db.execute("update login_throttle set locked_until = '2000-01-01T00:00:00Z'")
        db.commit()
    opened = client.post("/api/auth/login", json={"email": "brute@example.com", "password": "password-ok"})
    assert opened.status_code == 200
    with sqlite3.connect(client.db_path) as db:
        left = db.execute("select count(*) from login_throttle where email = 'brute@example.com'").fetchone()[0]
    assert left == 0


def test_visibility_flags_round_trip_without_copying_into_processing(client, capsys):
    register_and_confirm(client)
    created = client.put("/api/candidate/profile", json=_PROFILE)
    assert created.status_code == 200, created.text
    assert created.json()["show_name"] is True
    assert created.json()["show_experience"] is True
    assert created.json()["show_achievements"] is True
    assert created.json()["full_name"] == "Иван Синтетиков"
    assert created.json()["experience"] == "3 года"
    capsys.readouterr()
    hidden = client.put(
        "/api/candidate/profile",
        json={
            **_PROFILE,
            "full_name": "Иван Тайный",
            "experience": "закрытый стаж",
            "publication": False,
            "show_name": True,
            "show_experience": False,
            "show_achievements": True,
        },
    )
    assert hidden.status_code == 200, hidden.text
    own = client.get("/api/candidate/profile").json()
    assert own["full_name"] == "Иван Тайный"
    assert own["experience"] == "закрытый стаж"
    assert own["show_name"] is True
    assert own["show_experience"] is False
    assert own["publication"] is False
    line = [row for row in capsys.readouterr().out.splitlines() if "visibility_flag" in row][-1]
    assert line.endswith("visibility_flag 1 0 1")
    assert "Иван" not in line
    assert "закрытый" not in line
    assert "@" not in line
    with sqlite3.connect(client.db_path) as db:
        stored = db.execute(
            "select processing, publication, show_name, show_experience, show_achievements from consents"
        ).fetchone()
    assert stored == (0, 0, 1, 0, 1)
    again = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "full_name": "Иван Тайный", "experience": "закрытый стаж", "publication": True, "show_name": False},
    )
    assert again.status_code == 200, again.text
    assert again.json()["show_name"] is False
    assert again.json()["show_experience"] is False
    assert again.json()["show_achievements"] is True
    with sqlite3.connect(client.db_path) as db:
        stored = db.execute(
            "select processing, publication, show_name, show_experience, show_achievements from consents"
        ).fetchone()
    assert stored == (1, 1, 0, 0, 1)
    for key, value in (("show_name", "yes"), ("show_experience", 1), ("show_achievements", "false")):
        rejected = client.put("/api/candidate/profile", json={**_PROFILE, "full_name": "Иван Тайный", key: value})
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"] == "visibility_flag"
    assert client.get("/api/candidate/profile").json()["show_name"] is False
    kept_null = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "full_name": "Иван Тайный", "experience": "закрытый стаж", "show_name": None},
    )
    assert kept_null.status_code == 200, kept_null.text
    assert kept_null.json()["show_name"] is False
    omitted = {key: value for key, value in _PROFILE.items() if key != "publication"}
    omitted["full_name"] = "Иван Тайный"
    omitted["experience"] = "закрытый стаж"
    kept = client.put("/api/candidate/profile", json=omitted)
    assert kept.status_code == 200, kept.text
    assert kept.json()["show_name"] is False
    assert kept.json()["show_experience"] is False
    assert kept.json()["show_achievements"] is True
    assert kept.json()["publication"] is True
    assert kept.json()["full_name"] == "Иван Тайный"


def test_cabinet_unchecked_visibility_boxes_save_zero(client):
    register_and_confirm(client, email="boxes@example.com")
    page = client.get("/candidate")
    assert page.status_code == 200
    assert "Согласие на обработку персональных данных дано при регистрации." in page.text
    for label in (
        "Показывать работодателю ФИО",
        "Показывать работодателю стаж",
        "Показывать работодателю достижения ФСП",
    ):
        assert label in page.text
    assert 'name="show_name" value="1" checked' in page.text
    assert 'name="show_experience" value="1" checked' in page.text
    assert 'name="show_achievements" value="1" checked' in page.text
    token = _csrf(page.text)
    saved = client.post(
        "/candidate/profile",
        data={
            "csrf": token,
            "full_name": "Иван Синтетиков",
            "industry": "it",
            "specialization": "backend",
            "grade": "middle",
            "team_role": "разработчик",
            "work_format": "удалённо",
            "experience": "3 года",
            "external_id": "",
            "publication": "1",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303, saved.text
    body = client.get("/api/candidate/profile").json()
    assert body["show_name"] is False
    assert body["show_experience"] is False
    assert body["show_achievements"] is False
    assert body["full_name"] == "Иван Синтетиков"
    assert body["publication"] is True
    again = client.get("/candidate")
    assert 'name="show_name" value="1" checked' not in again.text
    assert 'name="show_experience" value="1" checked' not in again.text
    assert 'name="show_achievements" value="1" checked' not in again.text
    token = _csrf(again.text)
    restored = client.post(
        "/candidate/profile",
        data={
            "csrf": token,
            "full_name": "Иван Синтетиков",
            "industry": "it",
            "specialization": "backend",
            "grade": "middle",
            "team_role": "разработчик",
            "work_format": "удалённо",
            "experience": "3 года",
            "external_id": "",
            "publication": "1",
            "show_name": "1",
            "show_experience": "on",
            "show_achievements": "true",
        },
        follow_redirects=False,
    )
    assert restored.status_code == 303, restored.text
    body = client.get("/api/candidate/profile").json()
    assert body["show_name"] is True
    assert body["show_experience"] is True
    assert body["show_achievements"] is True

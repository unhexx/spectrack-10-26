"""Периодическое задание раз в 30 дней. Сдача не пишет балл и не меняет грейд."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from product.app.db import models as db_models
from product.app.db.session import _ADDED_COLUMNS, open_session
from product.app.services.categories import recompute
from product.app.services.clock import utc_now, whole_days_since
from product.matching.need import neighbor_phrase
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
_PROMPT = "Назовите один заголовок идемпотентного POST."
_ABOVE_NEIGHBOR = (
    "Выше соседней карточки не по полям подтверждён, балл, "
    "число достижений и приз."
)


def _login(client, email: str) -> None:
    logged = client.post("/api/auth/login", json={"email": email, "password": "password-ok"})
    assert logged.status_code == 200, logged.text


def _candidate(client, email: str, **extra) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json={**_PROFILE, **extra})
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _employer(client, email: str = "boss-periodic@example.com") -> None:
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
        "task_prompt": _PROMPT,
    }
    body.update(extra)
    return body


def _publish(client, **extra) -> dict:
    created = client.post("/api/employer/vacancies", json=_vacancy(**extra))
    assert created.status_code == 201, created.text
    return created.json()


def _ago(days: int) -> str:
    moment = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _tasks(client, profile_id: int | None = None) -> list[tuple]:
    sql = "select id, profile_id, vacancy_id, offered_at, completed_at from periodic_tasks"
    args: tuple = ()
    if profile_id is not None:
        sql += " where profile_id = ?"
        args = (profile_id,)
    sql += " order by id"
    with sqlite3.connect(client.db_path) as db:
        return db.execute(sql, args).fetchall()


def _count(client, table: str) -> int:
    with sqlite3.connect(client.db_path) as db:
        return db.execute(f"select count(*) from {table}").fetchone()[0]


def _stamp(client, row_id: int, **fields: str) -> None:
    sets = ", ".join(f"{name} = ?" for name in fields)
    with sqlite3.connect(client.db_path) as db:
        db.execute(
            f"update periodic_tasks set {sets} where id = ?",
            (*fields.values(), row_id),
        )
        db.commit()


def _confirm(profile_id: int, score: int = 90) -> None:
    db = open_session()
    try:
        now = utc_now()
        db.add(
            db_models.TestAttempt(
                profile_id=profile_id,
                specialization="backend",
                declared_grade="middle",
                template_id="bank",
                rule_version="v1",
                answers_json="[]",
                score=score,
                outcome="confirm",
                proposed_grade="middle",
                status="submitted",
                created_at=now,
                submitted_at=now,
            )
        )
        recompute(db, profile_id)
        db.commit()
    finally:
        db.close()


def _csrf(html: str) -> str:
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def _log_lines(text: str, event: str) -> list[str]:
    return [line for line in text.splitlines() if f" {event} " in line]


def test_service_does_not_read_the_grade_interval():
    import inspect

    from product.app.services import periodic

    source = inspect.getsource(periodic)
    assert "interval_days" not in source
    assert "TASK_OFFER_INTERVAL_DAYS = 30" in source
    assert "periodic_tasks" not in _ADDED_COLUMNS


def test_neighbor_phrase_reads_rank_from_the_tail_of_a_bonus_tuple():
    six = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-067-0000-0-00000002"),
        (0, 0, 0, 0, 0, "1-033-0009-1-00000001"),
    )
    assert six == "Выше соседней карточки по полю балл: 67 против 33."
    seven = neighbor_phrase(
        (0, 0, 0, 0, 0, 1, "1-010-0000-0-00000002"),
        (0, 0, 0, 0, 0, 0, "1-090-0000-0-00000001"),
    )
    assert seven == _ABOVE_NEIGHBOR
    assert "бонус" not in seven


def test_no_row_until_a_published_task_exists(client):
    denied = client.get("/api/candidate/periodic-task")
    assert denied.status_code == 401
    assert denied.json()["error"] == "unauthorized"
    register_and_confirm(client, email="bare-periodic@example.com")
    empty = client.get("/api/candidate/periodic-task")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "vacancy_id": None,
        "title": "",
        "prompt": "",
        "offered_at": "",
        "completed_at": "",
        "line": "ещё не сдавал",
    }
    missing = client.post("/api/candidate/periodic-task")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    _candidate(client, "idle-periodic@example.com")
    page = client.get("/candidate")
    assert page.status_code == 200
    assert "Заданий сейчас нет." in page.text
    assert _tasks(client) == []
    with sqlite3.connect(client.db_path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(periodic_tasks)")]
        indexes = [row[1] for row in db.execute("PRAGMA index_list(periodic_tasks)")]
    assert columns == ["id", "profile_id", "vacancy_id", "offered_at", "completed_at"]
    assert "ix_periodic_profile" in indexes
    assert "uq_periodic_open" in indexes
    _employer(client)
    forbidden = client.get("/api/candidate/periodic-task")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "forbidden"
    _login(client, "idle-periodic@example.com")
    still = client.get("/candidate")
    assert "Заданий сейчас нет." in still.text
    assert _tasks(client) == []


def test_offer_waits_thirty_days_and_a_miss_is_not_a_completion(client, capsys):
    profile_id = _candidate(client, "wait-periodic@example.com", full_name="Ждун Периодический")
    _employer(client)
    first = _publish(client, title="Первая", task_prompt="Первая формулировка")
    second = _publish(client, title="Вторая", task_prompt="Вторая формулировка")
    _login(client, "wait-periodic@example.com")
    opened = client.get("/api/candidate/periodic-task")
    assert opened.status_code == 200, opened.text
    body = opened.json()
    ordered = sorted((first["id"], second["id"]))
    assert body["vacancy_id"] == ordered[profile_id % 2]
    assert body["line"] == "ещё не сдавал"
    assert body["completed_at"] == ""
    assert body["prompt"] in {"Первая формулировка", "Вторая формулировка"}
    again = client.get("/api/candidate/periodic-task")
    assert again.json()["offered_at"] == body["offered_at"]
    assert len(_tasks(client, profile_id)) == 1
    row_id = _tasks(client, profile_id)[0][0]
    _stamp(client, row_id, offered_at=_ago(29))
    held = client.get("/api/candidate/periodic-task")
    assert held.json()["vacancy_id"] == body["vacancy_id"]
    assert len(_tasks(client, profile_id)) == 1
    expired = _ago(30)
    _stamp(client, row_id, offered_at=expired)
    replaced = client.get("/api/candidate/periodic-task")
    assert replaced.status_code == 200, replaced.text
    rows = _tasks(client, profile_id)
    assert len(rows) == 1
    assert rows[0][4] is None
    assert rows[0][3] > expired
    fresh_id = rows[0][0]
    done = client.post("/api/candidate/periodic-task")
    assert done.status_code == 200, done.text
    assert done.json()["line"] == "последнее задание 0 дней назад"
    assert done.json()["vacancy_id"] is None
    assert done.json()["title"] == ""
    assert done.json()["prompt"] == ""
    assert done.json()["offered_at"] == ""
    assert done.json()["completed_at"] == ""
    stayed = client.get("/api/candidate/periodic-task")
    assert stayed.json()["line"] == "последнее задание 0 дней назад"
    assert len(_tasks(client, profile_id)) == 1
    _stamp(client, fresh_id, completed_at=_ago(29))
    inside = client.get("/api/candidate/periodic-task")
    assert inside.json()["vacancy_id"] is None
    assert len(_tasks(client, profile_id)) == 1
    assert "Следующее задание через" in client.get("/candidate").text
    _stamp(client, fresh_id, completed_at=_ago(30))
    rotated = client.get("/api/candidate/periodic-task")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["vacancy_id"]
    history = _tasks(client, profile_id)
    assert len(history) == 2
    assert history[0][0] == fresh_id
    assert history[0][4] is not None
    assert history[1][4] is None
    lines = _log_lines(capsys.readouterr().out, "periodic_offered")
    assert lines
    for line in lines:
        assert line.endswith(f"periodic_offered {profile_id}")
        assert "@" not in line
        assert "Ждун" not in line
        assert "формулировка" not in line


def test_completion_does_not_move_grade_rank_or_attempts(client, capsys):
    profile_id = _candidate(client, "grade-periodic@example.com", full_name="Балл Неменяется")
    _employer(client, email="grade-boss@example.com")
    _publish(client)
    _login(client, "grade-periodic@example.com")
    grade = client.get("/api/candidate/profile").json()["grade"]
    rank_key = client.get("/api/candidate/category").json()["rank_key"]
    attempts = _count(client, "test_attempts")
    applications = _count(client, "applications")
    page = client.get("/candidate")
    assert _PROMPT in page.text
    assert "Backend middle" in page.text
    assert 'action="/candidate/periodic-task"' in page.text
    assert 'name="vacancy_id"' not in page.text
    posted = client.post(
        "/candidate/periodic-task",
        data={"csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert posted.headers["location"] == "/candidate"
    cabinet = client.get("/candidate")
    assert cabinet.status_code == 200
    assert "Следующее задание через 30 дней." in cabinet.text
    assert "Заданий сейчас нет." not in cabinet.text
    assert 'action="/candidate/periodic-task"' not in cabinet.text
    assert client.get("/api/candidate/profile").json()["grade"] == grade
    assert client.get("/api/candidate/category").json()["rank_key"] == rank_key
    assert _count(client, "test_attempts") == attempts
    assert _count(client, "applications") == applications
    rows = _tasks(client, profile_id)
    assert len(rows) == 1
    assert rows[0][4]
    done = _log_lines(capsys.readouterr().out, "periodic_completed")
    assert len(done) == 1
    assert done[0].endswith(f"periodic_completed {profile_id}")
    assert "@" not in done[0]
    assert "Неменяется" not in done[0]
    assert _PROMPT not in done[0]


def test_freshness_bonus_lifts_a_worse_rank_key_only_inside_the_window(client):
    low_id = _candidate(client, "low-periodic@example.com", full_name="Ниже Рангом")
    high_id = _candidate(client, "high-periodic@example.com", full_name="Выше Рангом")
    assert low_id < high_id
    _confirm(high_id, 90)
    _employer(client, email="rank-boss@example.com")
    _publish(client)
    _login(client, "low-periodic@example.com")
    low_rank = client.get("/api/candidate/category").json()["rank_key"]
    _login(client, "high-periodic@example.com")
    high_rank = client.get("/api/candidate/category").json()["rank_key"]
    assert low_rank < high_rank
    _login(client, "rank-boss@example.com")
    before = client.get("/api/employer/candidates").json()
    assert [row["profile_id"] for row in before] == [high_id, low_id]
    assert [row["task_line"] for row in before] == ["ещё не сдавал", "ещё не сдавал"]
    _login(client, "low-periodic@example.com")
    client.get("/api/candidate/periodic-task")
    done = client.post("/api/candidate/periodic-task")
    assert done.status_code == 200, done.text
    assert done.json()["line"] == "последнее задание 0 дней назад"
    assert client.get("/api/candidate/category").json()["rank_key"] == low_rank
    _login(client, "rank-boss@example.com")
    lifted = client.get("/api/employer/candidates").json()
    assert [row["profile_id"] for row in lifted] == [low_id, high_id]
    assert lifted[0]["task_line"] == "последнее задание 0 дней назад"
    assert lifted[1]["task_line"] == "ещё не сдавал"
    assert lifted[0]["rank_key"] == low_rank
    assert _ABOVE_NEIGHBOR in lifted[0]["list_reason"]
    assert "бонус" not in lifted[0]["list_reason"]
    page = client.get("/employer/shortlist", params={"category_id": "backend:middle"})
    assert "последнее задание 0 дней назад" in page.text
    assert "ещё не сдавал" in page.text
    row_id = _tasks(client, low_id)[0][0]
    stale = _ago(31)
    _stamp(client, row_id, completed_at=stale)
    aged = client.get("/api/employer/candidates").json()
    assert [row["profile_id"] for row in aged] == [high_id, low_id]
    assert aged[0]["task_line"] == "ещё не сдавал"
    assert aged[1]["task_line"] == f"последнее задание {whole_days_since(stale)} дней назад"
    assert whole_days_since(stale) >= 31
    assert aged[1]["rank_key"] == low_rank


def test_other_profile_cannot_close_the_offer(client):
    owner = _candidate(client, "owner-periodic@example.com")
    other = _candidate(client, "other-periodic@example.com")
    _employer(client, email="alien-boss@example.com")
    vacancy = _publish(client)
    _login(client, "owner-periodic@example.com")
    opened = client.get("/api/candidate/periodic-task")
    assert opened.status_code == 200, opened.text
    _login(client, "other-periodic@example.com")
    stolen = client.post(
        "/api/candidate/periodic-task",
        json={"vacancy_id": vacancy["id"], "profile_id": owner},
    )
    assert stolen.status_code == 404
    assert stolen.json()["error"] == "not_found"
    assert _tasks(client, owner)[0][4] is None
    assert _tasks(client, other) == []
    other_open = client.get("/api/candidate/periodic-task")
    assert other_open.json()["vacancy_id"] == vacancy["id"]
    closed = client.post("/api/candidate/periodic-task", json={"profile_id": owner})
    assert closed.status_code == 200, closed.text
    assert _tasks(client, owner)[0][4] is None
    assert _tasks(client, other)[0][4] is not None


def test_dead_vacancy_is_not_a_completion(client):
    lost_id = _candidate(client, "lost-periodic@example.com")
    closed_id = _candidate(client, "closed-periodic@example.com")
    _employer(client, email="dead-boss@example.com")
    lost = _publish(client, title="С заданием", task_prompt="Формулировка которая пропадёт")
    _login(client, "lost-periodic@example.com")
    lost_view = client.get("/api/candidate/periodic-task").json()
    assert lost_view["vacancy_id"] == lost["id"]
    _login(client, "dead-boss@example.com")
    cleared = _vacancy(title="С заданием")
    del cleared["task_prompt"]
    updated = client.post(f"/api/employer/vacancies/{lost['id']}", json=cleared)
    assert updated.status_code == 200, updated.text
    assert updated.json()["task_prompt"] == ""
    closed = _publish(client, title="Снимут", task_prompt="Формулировка снятой")
    _login(client, "closed-periodic@example.com")
    closed_view = client.get("/api/candidate/periodic-task").json()
    assert closed_view["vacancy_id"] == closed["id"]
    with sqlite3.connect(client.db_path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("update vacancies set status = 'closed' where id = ?", (closed["id"],))
        db.commit()
        stored = db.execute("select status from vacancies where id = ?", (closed["id"],)).fetchone()
    assert stored == ("closed",)
    _login(client, "lost-periodic@example.com")
    lost_post = client.post("/api/candidate/periodic-task")
    assert lost_post.status_code == 404
    assert lost_post.json()["error"] == "not_found"
    lost_again = client.get("/api/candidate/periodic-task").json()
    assert lost_again["vacancy_id"] is None
    assert lost_again["title"] == ""
    assert lost_again["prompt"] == ""
    assert lost_again["offered_at"] == ""
    assert lost_again["completed_at"] == ""
    assert lost_again["line"] == "ещё не сдавал"
    lost_page = client.get("/candidate")
    assert "Следующее задание через 30 дней." in lost_page.text
    assert "Заданий сейчас нет." not in lost_page.text
    assert 'action="/candidate/periodic-task"' not in lost_page.text
    assert _tasks(client, lost_id)[0][4] is None
    _login(client, "closed-periodic@example.com")
    closed_post = client.post("/api/candidate/periodic-task")
    assert closed_post.status_code == 404
    assert closed_post.json()["error"] == "not_found"
    closed_again = client.get("/api/candidate/periodic-task").json()
    assert closed_again["vacancy_id"] is None
    assert closed_again["offered_at"] == ""
    assert closed_again["completed_at"] == ""
    assert closed_again["line"] == "ещё не сдавал"
    closed_page = client.get("/candidate")
    assert "Следующее задание через 30 дней." in closed_page.text
    assert "Заданий сейчас нет." not in closed_page.text
    assert _tasks(client, closed_id)[0][4] is None
    _login(client, "dead-boss@example.com")
    cards = client.get("/api/employer/candidates").json()
    by_id = {row["profile_id"]: row for row in cards}
    assert by_id[lost_id]["task_line"] == "ещё не сдавал"
    assert by_id[closed_id]["task_line"] == "ещё не сдавал"


def test_insert_race_returns_the_same_open_row(client, monkeypatch):
    profile_id = _candidate(client, "race-periodic@example.com")
    _employer(client, email="race-boss@example.com")
    vacancy = _publish(client)
    _login(client, "race-periodic@example.com")
    first = client.get("/api/candidate/periodic-task")
    assert first.status_code == 200, first.text
    assert first.json()["vacancy_id"] == vacancy["id"]
    offered_at = first.json()["offered_at"]
    row_id = _tasks(client, profile_id)[0][0]

    import product.app.services.periodic as periodic

    monkeypatch.setattr(periodic, "_latest", lambda _db, _profile_id: None)
    raced = client.get("/api/candidate/periodic-task")
    assert raced.status_code == 200, raced.text
    assert raced.json()["vacancy_id"] == vacancy["id"]
    assert raced.json()["offered_at"] == offered_at
    assert "error" not in raced.json()
    rows = _tasks(client, profile_id)
    assert len(rows) == 1
    assert rows[0][0] == row_id
    assert rows[0][4] is None

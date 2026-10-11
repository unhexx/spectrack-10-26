"""HTML сдачи теста: вопросы по одному, оценка каждого варианта, без возврата
назад, панель итога с уверенностью. ТЗ §2.1 «Прохождение тестирования»,
критерий оценки 3 «понятность сквозного пути»."""

from __future__ import annotations

import re
import sqlite3

from product.tests.conftest import register_and_confirm, stored_form

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
_LEVELS = ("junior", "middle", "senior", "lead")


def _seeker(client, email: str = "ux@example.com", **extra) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json={**_PROFILE, **extra})
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _csrf(html: str) -> str:
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def _status(client, attempt_id: int) -> str:
    with sqlite3.connect(client.db_path) as db:
        return db.execute("select status from test_attempts where id = ?", (attempt_id,)).fetchone()[0]


def _start_page(client) -> int:
    page = client.get("/candidate/test")
    sent = client.post("/candidate/test/start", data={"csrf": _csrf(page.text)}, follow_redirects=False)
    assert sent.status_code == 303
    with sqlite3.connect(client.db_path) as db:
        return db.execute("select max(id) from test_attempts").fetchone()[0]


def _current(client, attempt_id: int) -> dict:
    form = stored_form(client, attempt_id)
    done = form.get("answers") or {}
    return next(item for item in form["items"] if item["qid"] not in done)


def _marks(item: dict, correct: bool) -> dict[str, str]:
    marks = {}
    for option in item["options"]:
        right = option["id"] == item["key"]
        marks[f"mark_{option['id']}"] = "true" if right == correct else "false"
    return marks


def _payload(item: dict, correct: bool) -> dict[str, str]:
    """Поля формы: judge — оценки вариантов, числовой тип — само число."""
    if item.get("options"):
        return _marks(item, correct)
    wrong = 0 if item["key"] != 0 else 4
    return {"value": str(item["key"] if correct else wrong)}


def _stored_value(item: dict, correct: bool):
    if item.get("type") in ("multi", "order", "numeric"):
        return item["key"] if correct else (0 if item["key"] != 0 else 4)
    return [item["key"]] if correct else []


def _answer(client, attempt_id: int, item: dict, fields: dict[str, str]):
    page = client.get("/candidate/test")
    data = {"csrf": _csrf(page.text), "attempt_id": str(attempt_id), "qid": item["qid"], **fields}
    return client.post("/candidate/test/answer", data=data, follow_redirects=False)


def _until(client, attempt_id: int, kind: str) -> dict:
    """Дойти до вопроса нужного вида, предыдущие сдать верно."""
    for _ in range(30):
        item = _current(client, attempt_id)
        if kind == "judge" and item.get("options"):
            return item
        if kind == "number" and not item.get("options"):
            return item
        assert _answer(client, attempt_id, item, _payload(item, True)).status_code == 303
    raise AssertionError(kind)


def _shows(page_text: str, item: dict) -> None:
    assert item["text"] in page_text
    assert "correct" not in page_text and '"key"' not in page_text
    assert "<noscript>" not in page_text
    if item.get("options"):
        assert "Оцените каждый вариант." in page_text
        radios = re.findall(r'type="radio" name="mark_([0-9a-f]+)" value="(true|false)"', page_text)
        assert len(radios) == 6
        assert {choice for choice, _ in radios} == {opt["id"] for opt in item["options"]}
    else:
        assert "Введите число" in page_text
        assert 'name="value"' in page_text
        assert "Оцените каждый вариант." not in page_text


def test_draft_page_shows_judge_and_number(client):
    _seeker(client)
    attempt_id = _start_page(client)
    page = client.get("/candidate/test")
    assert page.status_code == 200
    assert "Вопрос 1 из 12" in page.text
    seen = set()
    for _ in range(12):
        item = _current(client, attempt_id)
        page = client.get("/candidate/test")
        _shows(page.text, item)
        seen.add("judge" if item.get("options") else item["type"])
        if not item.get("options") and "number-rejected" not in seen:
            rejected = _answer(client, attempt_id, item, {"value": "нет"})
            assert rejected.status_code == 422
            assert item["qid"] not in (stored_form(client, attempt_id).get("answers") or {})
            seen.add("number-rejected")
        if "judge" in seen and seen & {"multi", "order", "numeric"} and "number-rejected" in seen:
            break
        assert _answer(client, attempt_id, item, _payload(item, True)).status_code == 303
    assert "judge" in seen and seen & {"multi", "order", "numeric"} and "number-rejected" in seen


def test_answer_advances_and_answered_question_cannot_be_reopened(client):
    _seeker(client, "advance@example.com")
    attempt_id = _start_page(client)
    first = _current(client, attempt_id)
    sent = _answer(client, attempt_id, first, _payload(first, True))
    assert sent.status_code == 303
    page = client.get("/candidate/test")
    assert "Вопрос 2 из 12" in page.text
    assert first["text"] not in page.text
    again = _answer(client, attempt_id, first, _payload(first, False))
    assert again.status_code == 422
    assert stored_form(client, attempt_id)["answers"][first["qid"]] == _stored_value(first, True)


def test_incomplete_or_bad_marks_keep_the_question(client):
    _seeker(client, "marks@example.com")
    attempt_id = _start_page(client)
    item = _until(client, attempt_id, "judge")
    marks = _marks(item, True)
    partial = dict(list(marks.items())[:2])
    short = _answer(client, attempt_id, item, partial)
    assert short.status_code == 422
    assert "Что случилось" in short.text
    bad = _answer(client, attempt_id, item, {key: "maybe" for key in marks})
    assert bad.status_code == 422
    foreign = _answer(client, attempt_id, item, {"mark_deadbeef": "true", **marks})
    assert foreign.status_code == 422
    assert item["qid"] not in (stored_form(client, attempt_id).get("answers") or {})
    no_csrf = client.post(
        "/candidate/test/answer",
        data={"csrf": "wrong", "attempt_id": str(attempt_id), "qid": item["qid"], **marks},
        follow_redirects=False,
    )
    assert no_csrf.status_code in (400, 403)
    assert item["qid"] not in (stored_form(client, attempt_id).get("answers") or {})


def test_all_false_marks_count_as_wrong_answer(client):
    _seeker(client, "false@example.com")
    attempt_id = _start_page(client)
    item = _until(client, attempt_id, "judge")
    sent = _answer(client, attempt_id, item, {f"mark_{o['id']}": "false" for o in item["options"]})
    assert sent.status_code == 303
    assert stored_form(client, attempt_id)["answers"][item["qid"]] == []


def test_page_walk_with_extra_blocks_ends_in_outcome_panel(client):
    _seeker(client, "walk@example.com")
    attempt_id = _start_page(client)
    totals = set()
    for _ in range(40):
        if _status(client, attempt_id) == "submitted":
            break
        item = _current(client, attempt_id)
        totals.add(len(stored_form(client, attempt_id)["items"]))
        ok = _LEVELS.index(item["level"]) <= _LEVELS.index("middle")
        assert _answer(client, attempt_id, item, _payload(item, ok)).status_code == 303
    assert _status(client, attempt_id) == "submitted"
    page = client.get("/candidate/test")
    assert "Итог попытки" in page.text
    assert "Верных ответов:" in page.text
    assert "Уверенность" in page.text
    assert re.search(r"статус «(уверенно|у границы)»", page.text)
    assert "заявленный грейд подтверждён" in page.text
    assert min(totals) == 12


def test_outcome_panel_uses_recorded_pair(client):
    _seeker(client, "panel@example.com")
    attempt_id = _start_page(client)
    for _ in range(40):
        if _status(client, attempt_id) == "submitted":
            break
        item = _current(client, attempt_id)
        assert _answer(client, attempt_id, item, _payload(item, True)).status_code == 303
    page = client.get("/candidate/test")
    assert "Грейд попытки: middle." in page.text
    assert "Верных ответов: 100%" in page.text
    assert "test-path-current" in page.text
    assert "предложен грейд выше senior" in page.text
    cabinet = client.get("/candidate")
    assert "верных 100%" in cabinet.text


def test_draft_without_form_is_rebuilt(client):
    """Черновик без сохранённого варианта (form_json пуст) не блокирует тест:
    кнопка «Начать» собирает вариант в той же строке."""
    profile_id = _seeker(client, "noform@example.com")
    with sqlite3.connect(client.db_path) as db:
        db.execute(
            "insert into test_attempts (profile_id, specialization, declared_grade, template_id, rule_version,"
            " status, created_at) values (?, 'backend', 'middle', 'bank', 'v1', 'draft', '2026-10-01T00:00:00Z')",
            (profile_id,),
        )
        db.commit()
    page = client.get("/candidate/test")
    assert "Начать тест записанного грейда" in page.text
    attempt_id = _start_page(client)
    assert stored_form(client, attempt_id)["items"]
    assert "Вопрос 1 из 12" in client.get("/candidate/test").text

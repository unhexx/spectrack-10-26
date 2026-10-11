"""Испытание грейда: вариант, ключ на сервере, интервал и смена
грейда только после confirm. ТЗ §2, §2.1 «Категория и грейд»."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import product
from product.app.services.clock import utc_now
from product.app.services.tests import is_adjacent_grade, is_lower_grade
from product.matching import assessment
from product.matching.stems import catalog
from product.matching.rules import load_rules
from product.tests.conftest import answers_by_mode, register_and_confirm, stored_form, take_test

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


def _seeker(client, email: str = "seeker@example.com", **extra) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json={**_PROFILE, **extra})
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _login(client, email: str) -> None:
    logged = client.post("/api/auth/login", json={"email": email, "password": "password-ok"})
    assert logged.status_code == 200, logged.text


def _submit(client, attempt_id: int, answers: list[str]):
    return client.post(f"/api/candidate/tests/{attempt_id}/submit", json={"answers": answers})


def _stamp(client, profile_id: int) -> str:
    with sqlite3.connect(client.db_path) as db:
        return db.execute("select grade_changed_at from profiles where id = ?", (profile_id,)).fetchone()[0]


def _set_stamp(client, profile_id: int, stamp: str) -> None:
    with sqlite3.connect(client.db_path) as db:
        db.execute("update profiles set grade_changed_at = ? where id = ?", (stamp, profile_id))
        db.commit()


def _drafts(client, profile_id: int) -> int:
    with sqlite3.connect(client.db_path) as db:
        return db.execute(
            "select count(*) from test_attempts where profile_id = ? and status = 'draft'",
            (profile_id,),
        ).fetchone()[0]


def _status(client, attempt_id: int) -> str:
    with sqlite3.connect(client.db_path) as db:
        return db.execute("select status from test_attempts where id = ?", (attempt_id,)).fetchone()[0]


def _csrf(html: str) -> str:
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def test_lower_grade_follows_scale_not_adjacency():
    assert is_lower_grade("middle", "junior") is True
    assert is_lower_grade("junior", "middle") is False
    assert is_lower_grade("lead", "senior") is True
    assert is_lower_grade("junior", "lead") is False
    assert is_lower_grade("lead", "junior") is True
    assert is_adjacent_grade("lead", "junior") is False
    assert is_adjacent_grade("middle", "junior") is True
    assert is_lower_grade("middle", "unknown") is False
    assert is_adjacent_grade("middle", "unknown") is False
    assert is_lower_grade("ghost", "junior") is False
    assert is_adjacent_grade("ghost", "junior") is False


def test_forms_follow_plan_and_stay_offline():
    """ТЗ §2: план одинаков для всех, конкретные вопросы — разные; ключа нет в публичном виде."""
    rules = load_rules()
    root = Path(product.__file__).resolve().parent / "matching"
    source = (root / "assessment.py").read_text(encoding="utf-8")
    assert "sqlalchemy" not in source
    seen_sets = []
    seen_types: set[str] = set()
    for spec in rules["specializations"]:
        for grade in rules["grades"]:
            plan = {part["level"]: part["count"] for part in assessment.blueprint(grade)}
            assert sum(plan.values()) == 12
            form = assessment.new_form(spec, grade, assessment.History(), assessment.Exposure())
            assert len(form["items"]) == 12
            levels = {}
            for item in form["items"]:
                levels[item["level"]] = levels.get(item["level"], 0) + 1
            assert levels == plan
            texts = [item["text"] for item in form["items"]]
            assert len(texts) == len(set(texts))
            public = assessment.public_questions(form["items"])
            blob = json.dumps(public, ensure_ascii=False)
            for item in form["items"]:
                if item.get("options"):
                    assert item["type"] == "judge"
                    assert item["key"] in {c["id"] for c in item["options"]}
                else:
                    assert item["type"] in {"multi", "order", "numeric"}
                    assert isinstance(item["key"], int) and item["key"] not in (0, 1, 2)
                seen_types.add(item["type"])
            assert '"key"' not in blob and '"b":' not in blob and "family" not in blob
            for question in public:
                assert {c["id"] for c in question["choices"]}.isdisjoint({"a", "b", "c"})
            seen_sets.append(frozenset(item["item_id"] for item in form["items"]))
    assert "judge" in seen_types and seen_types & {"multi", "order", "numeric"}
    again = assessment.new_form("backend", "middle", assessment.History(), assessment.Exposure())
    first = assessment.new_form("backend", "middle", assessment.History(), assessment.Exposure())
    assert [i["key"] for i in again["items"]] != [i["key"] for i in first["items"]]
    samples = json.loads((root / "vacancies" / "samples.json").read_text(encoding="utf-8"))
    assert samples["vacancies"]
    for item in samples["vacancies"]:
        assert item["grade"] in rules["grades"] and item["specialization"] in rules["specializations"]


def test_api_submit_accepts_judge_list_and_number(client):
    """API принимает и список id judge, и число multi/order/numeric."""
    _seeker(client, "both-types@example.com")
    started = client.post("/api/candidate/tests/start", json={})
    assert started.status_code == 201, started.text
    attempt_id = started.json()["attempt_id"]
    form = stored_form(client, attempt_id)
    types = {item["type"] for item in form["items"]}
    assert "judge" in types and types & {"multi", "order", "numeric"}
    answers = answers_by_mode(client, attempt_id, "middle", "higher")
    for item in form["items"]:
        if item["type"] == "judge":
            assert answers[item["qid"]] == [item["key"]]
        else:
            assert answers[item["qid"]] == item["key"]
    sent = client.post(f"/api/candidate/tests/{attempt_id}/submit", json={"answers": answers})
    assert sent.status_code == 200, sent.text
    stored = stored_form(client, attempt_id)
    for item in form["items"]:
        got = stored["answers"][item["qid"]]
        if item["type"] == "judge":
            assert got == [item["key"]]
        else:
            assert got == item["key"]


def test_catalog_is_unique_and_feeds_the_bank():
    """Каталог вопросов — источник банка (ТЗ §2 «Содержание и формат заданий
    команда определяет сама»): 72 набора по 3 вопроса, у каждого вопроса один
    верный вариант из трёх, тексты уникальны, слоты набора — три темы."""
    rules = load_rules()
    root = Path(product.__file__).resolve().parent / "matching"
    assert not list((root / "templates").glob("*.json"))
    assert not (root / "grade.py").exists()
    rows = catalog()
    ids = [row["set_id"] for row in rows]
    assert len(ids) == len(set(ids)) == 72
    families = ("refusal_contract", "close_cause", "offline_check")
    texts = []
    for row in rows:
        assert row["specialization"] in rules["specializations"] and row["grade"] in rules["grades"]
        assert len(row["questions"]) == 3
        for index, question in enumerate(row["questions"]):
            assert {choice["id"] for choice in question["choices"]} == {"a", "b", "c"}
            assert question["correct"] in {"a", "b", "c"}
            assert question["family"] == families[index]
            texts.append(question["text"])
    assert len(texts) == len(set(texts)) == 216
    pairs = {(row["specialization"], row["grade"]) for row in rows}
    assert len(pairs) == len(rules["specializations"]) * len(rules["grades"])

def test_retake_uses_other_families():
    first = assessment.new_form("data", "senior", assessment.History(), assessment.Exposure())
    seen = assessment.history_from([first])
    second = assessment.new_form("data", "senior", seen, assessment.exposure_from([first]))
    at_first = {i["family_id"] for i in first["items"] if i["slot"] == "at"}
    at_second = {i["family_id"] for i in second["items"] if i["slot"] == "at"}
    pool = {
        fam["family_id"]
        for fam in assessment.bank_index().families("data", "senior")
        if fam["family_id"] not in assessment.pilot()["retired"]
    }
    # Шесть тем уровня. Сначала невиданные; повтор — только если действующих меньше двенадцати.
    assert at_first <= pool and at_second <= pool
    assert len(at_first & at_second) == max(0, 12 - len(pool))


def test_recorded_attempt_confirms_without_moving_grade(client):
    register_and_confirm(client)
    naked = client.post("/api/candidate/tests/start", json={})
    assert naked.status_code == 409
    assert naked.json()["error"] == "survey_required"
    saved = client.put("/api/candidate/profile", json=_PROFILE)
    assert saved.status_code == 200, saved.text
    profile_id = saved.json()["profile_id"]
    stamp = _stamp(client, profile_id)
    started = client.post("/api/candidate/tests/start", json={})
    assert started.status_code == 201, started.text
    body = started.json()
    assert "correct" not in json.dumps(body) and '"key"' not in json.dumps(body)
    dumped = json.dumps(body)
    assert "family" not in dumped
    assert "refusal_contract" not in dumped
    assert "close_cause" not in dumped
    assert "offline_check" not in dumped
    assert body["declared_grade"] == "middle"
    assert body["template_id"] == assessment.ENGINE
    assert body["total"] == 12 and len(body["questions"]) == 12
    explicit = client.post("/api/candidate/tests/start", json={"grade": "middle"})
    assert explicit.status_code == 201
    assert explicit.json()["attempt_id"] == body["attempt_id"]
    unknown = _submit(client, body["attempt_id"], [["nope"]] + [[] for _ in range(11)])
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "answer_unknown"
    assert _status(client, body["attempt_id"]) == "draft"
    short = _submit(client, body["attempt_id"], [[]])
    assert short.status_code == 422
    confirmed = take_test(client, body, "confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "submitted"
    assert 0 < confirmed.json()["score"] < 100
    assert confirmed.json()["test_status"] in {"уверенно", "у границы"}
    assert 0 <= confirmed.json()["confidence"] <= 1
    assert confirmed.json()["n_items"] >= 12
    assert confirmed.json()["outcome"] == "confirm"
    assert confirmed.json()["proposed_grade"] == "middle"
    assert confirmed.json()["grade"] == "middle"
    assert confirmed.json()["category_id"] == "backend:middle"
    assert client.get("/api/candidate/category").json()["test_score"] == confirmed.json()["score"]
    assert _stamp(client, profile_id) == stamp
    closed = _submit(client, body["attempt_id"], [])
    assert closed.status_code == 409
    assert closed.json()["error"] == "attempt_closed"
    # Прошлые ответы учитываются (накопление): после confirm одна сильная
    # попытка может снова дать confirm; повторяем, пока тест не предложит выше.
    for _ in range(4):
        again = client.post("/api/candidate/tests/start", json={})
        assert again.status_code == 201
        higher = take_test(client, again.json(), "higher")
        if higher.json()["outcome"] == "suggest_higher":
            break
    assert higher.status_code == 200, higher.text
    assert higher.json()["score"] == 100
    assert higher.json()["outcome"] == "suggest_higher"
    assert higher.json()["proposed_grade"] == "senior"
    assert higher.json()["grade"] == "middle"
    assert _stamp(client, profile_id) == stamp
    history = client.get("/api/candidate/tests").json()
    assert history[0]["attempt_id"] == again.json()["attempt_id"]
    assert history[-1]["attempt_id"] == body["attempt_id"]
    assert history[0]["test_status"] in {"уверенно", "у границы"}
    assert "correct" not in json.dumps(history)


def test_adjacent_grade_respects_offer_and_interval(client):
    profile_id = _seeker(client, "adj@example.com")
    early = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert early.status_code == 409
    assert early.json()["error"] == "grade_not_offered"
    assert _drafts(client, profile_id) == 0
    started = client.post("/api/candidate/tests/start", json={})
    offered = take_test(client, started.json(), "higher")
    assert offered.json()["proposed_grade"] == "senior"
    assert client.post("/api/candidate/tests/start", json={"grade": "lead"}).json()["error"] == "grade_not_offered"
    assert client.post("/api/candidate/tests/start", json={"grade": "junior"}).json()["error"] == "grade_not_offered"
    open_draft = client.post("/api/candidate/tests/start", json={})
    conflict = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "attempt_open"
    assert _drafts(client, profile_id) == 1
    take_test(client, open_draft.json(), "higher")
    survey_stamp = _stamp(client, profile_id)
    adjacent = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert adjacent.status_code == 201, adjacent.text
    assert adjacent.json()["declared_grade"] == "senior"
    assert adjacent.json()["template_id"] == assessment.ENGINE
    failed = take_test(client, adjacent.json(), "lower")
    assert failed.status_code == 200, failed.text
    assert failed.json()["score"] < 50
    assert failed.json()["outcome"] == "suggest_lower"
    assert failed.json()["grade"] == "middle"
    assert _stamp(client, profile_id) == survey_stamp
    retry = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert retry.status_code == 201, retry.text
    assert retry.json()["declared_grade"] == "senior"


def test_confirmed_adjacent_grade_replaces_category(client):
    profile_id = _seeker(client, "move-grade@example.com")
    started = client.post("/api/candidate/tests/start", json={})
    recorded = take_test(client, started.json(), "higher")
    assert recorded.json()["category_id"] == "backend:middle"
    register_and_confirm(client, email="boss-grade@example.com", role="employer")

    def listed(category_id: str) -> list[dict]:
        response = client.get("/api/employer/candidates", params={"category_id": category_id})
        assert response.status_code == 200, response.text
        return response.json()

    assert profile_id in {card["profile_id"] for card in listed("backend:middle")}
    assert profile_id not in {card["profile_id"] for card in listed("backend:senior")}
    _login(client, "move-grade@example.com")
    _set_stamp(client, profile_id, "2000-01-01T00:00:00Z")
    adjacent = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert adjacent.status_code == 201, adjacent.text
    _login(client, "boss-grade@example.com")
    assert profile_id in {card["profile_id"] for card in listed("backend:middle")}
    assert profile_id not in {card["profile_id"] for card in listed("backend:senior")}
    _login(client, "move-grade@example.com")
    done = take_test(client, adjacent.json(), "confirm")
    assert done.status_code == 200, done.text
    assert done.json()["outcome"] == "confirm"
    assert 0 < done.json()["score"] < 100
    assert done.json()["grade"] == "senior"
    assert done.json()["category_id"] == "backend:senior"
    assert _stamp(client, profile_id) != "2000-01-01T00:00:00Z"
    _login(client, "boss-grade@example.com")
    middle = listed("backend:middle")
    senior = listed("backend:senior")
    assert profile_id not in {card["profile_id"] for card in middle}
    card = next(item for item in senior if item["profile_id"] == profile_id)
    assert card["test_score"] == done.json()["score"]
    assert card["grade_status"].startswith("подтверждён")
    assert "email" not in card


def test_edges_keep_recorded_grade(client):
    junior_id = _seeker(client, "jun@example.com", grade="junior")
    started = client.post("/api/candidate/tests/start", json={})
    done = take_test(client, started.json(), "lower")
    assert done.json()["outcome"] == "suggest_lower"
    # ниже junior грейда нет: предложения «ниже» нет, грейд не понижается
    assert done.json()["proposed_grade"] is None
    assert done.json()["grade"] == "junior"
    assert _stamp(client, junior_id)

    _seeker(client, "lead@example.com", grade="lead")
    started = client.post("/api/candidate/tests/start", json={})
    done = take_test(client, started.json(), "higher")
    assert done.json()["score"] == 100
    assert done.json()["outcome"] == "confirm"
    assert done.json()["proposed_grade"] == "lead"
    assert done.json()["grade"] == "lead"

    senior_id = _seeker(client, "sen@example.com", grade="senior")
    started = client.post("/api/candidate/tests/start", json={})
    done = take_test(client, started.json(), "lower")
    assert done.json()["proposed_grade"] == "middle"
    assert done.json()["grade"] == "senior"
    _set_stamp(client, senior_id, "2000-01-01T00:00:00Z")
    adjacent = client.post("/api/candidate/tests/start", json={"grade": "middle"})
    assert adjacent.status_code == 201, adjacent.text
    higher = take_test(client, adjacent.json(), "higher")
    # Ответы провальной попытки senior учитываются, поэтому итог — confirm
    # (грейд записывается как middle) или suggest_higher (грейд остаётся senior).
    if higher.json()["outcome"] == "suggest_higher":
        assert higher.json()["proposed_grade"] == "senior"
        assert higher.json()["grade"] == "senior"
        assert _stamp(client, senior_id) == "2000-01-01T00:00:00Z"
    else:
        assert higher.json()["outcome"] == "confirm"
        assert higher.json()["grade"] == "middle"
        assert _stamp(client, senior_id) != "2000-01-01T00:00:00Z"

    _seeker(client, "own@example.com")
    attempt_id = client.post("/api/candidate/tests/start", json={}).json()["attempt_id"]
    _seeker(client, "other@example.com")
    foreign = _submit(client, attempt_id, [])
    assert foreign.status_code == 404
    assert foreign.json()["error"] == "not_found"
    missing = _submit(client, 999999, [])
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    overflow = _submit(client, 10**30, [])
    assert overflow.status_code == 404
    assert overflow.json()["error"] == "not_found"
    register_and_confirm(client, email="bare@example.com")
    bare = client.post("/api/candidate/tests/1/submit", json={"answers": []})
    assert bare.status_code == 404
    assert bare.json()["error"] == "not_found"
    register_and_confirm(client, email="boss-edge@example.com", role="employer")
    denied = client.post("/api/candidate/tests/start", json={})
    assert denied.status_code == 403
    assert denied.json()["error"] == "forbidden"
    assert client.get("/candidate/test").status_code == 403


def test_page_shows_attempt_history_and_vacancy_link(client):
    anon = client.get("/candidate/test", follow_redirects=False)
    assert anon.status_code == 303
    _seeker(client, "page@example.com")
    started = client.post("/api/candidate/tests/start", json={})
    body = started.json()
    page = client.get("/candidate/test")
    assert page.status_code == 200
    assert body["questions"][0]["text"] in page.text
    assert "Вопрос 1 из 12" in page.text
    assert "принять грейд" not in page.text.casefold()
    assert "Синтетическая вакансия backend middle" in page.text
    assert "3 — уровень ниже (junior), 6 — заявленный уровень (middle), 3 — уровень выше (senior)" in page.text
    # Ответы по одному через страницу: все верно → предложен грейд выше
    for _ in range(40):
        form = stored_form(client, body["attempt_id"])
        open_items = [i for i in form["items"] if i["qid"] not in (form.get("answers") or {})]
        if not open_items:
            break
        item = open_items[0]
        page = client.get("/candidate/test")
        data = {"csrf": _csrf(page.text), "attempt_id": str(body["attempt_id"]), "qid": item["qid"]}
        if item.get("options"):
            for option in item["options"]:
                data[f"mark_{option['id']}"] = "true" if option["id"] == item["key"] else "false"
        else:
            data["value"] = str(item["key"])
        sent = client.post("/candidate/test/answer", data=data, follow_redirects=False)
        assert sent.status_code == 303
        if _status(client, body["attempt_id"]) == "submitted":
            break
    assert _status(client, body["attempt_id"]) == "submitted"
    again = client.get("/candidate/test")
    assert "Верных ответов: 100%" in again.text
    assert "предложен грейд выше" in again.text
    assert "Уверенность" in again.text
    assert "Начать тест грейда senior" in again.text
    assert "Что случилось" not in again.text
    assert "ещё нельзя" not in again.text
    assert "grade_interval" not in again.text
    click = client.post(
        "/candidate/test/start",
        data={"csrf": _csrf(again.text), "grade": "senior"},
        follow_redirects=False,
    )
    assert click.status_code == 303
    assert "Что случилось" not in click.text


def test_first_lower_attempt_skips_interval(client):
    _seeker(client, "first-down@example.com")
    started = client.post("/api/candidate/tests/start", json={})
    failed = take_test(client, started.json(), "lower")
    assert failed.json()["outcome"] == "suggest_lower"
    assert failed.json()["proposed_grade"] == "junior"
    assert failed.json()["grade"] == "middle"
    down = client.post("/api/candidate/tests/start", json={"grade": "junior"})
    assert down.status_code == 201, down.text
    assert down.json()["declared_grade"] == "junior"


def test_resume_grade_does_not_burn_first_lower_attempt(client):
    profile_id = _seeker(client, "pdf-down@example.com")
    stamp = _stamp(client, profile_id)
    pdf = Path(product.__file__).resolve().parent / "matching" / "golden" / "resumes" / "full.pdf"
    uploaded = client.post(
        "/api/candidate/resume",
        files={"file": ("full.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["grade"] == "senior"
    assert _stamp(client, profile_id) == stamp
    started = client.post("/api/candidate/tests/start", json={})
    assert started.json()["declared_grade"] == "senior"
    failed = take_test(client, started.json(), "lower")
    assert failed.json()["outcome"] == "suggest_lower"
    assert failed.json()["proposed_grade"] == "middle"
    assert failed.json()["grade"] == "senior"
    down = client.post("/api/candidate/tests/start", json={"grade": "middle"})
    assert down.status_code == 201, down.text
    assert down.json()["declared_grade"] == "middle"
    assert _stamp(client, profile_id) == stamp


def test_hundred_on_lower_grade_does_not_burn_retry(client):
    _seeker(client, "retry-down@example.com")
    recorded = client.post("/api/candidate/tests/start", json={})
    confirmed = take_test(client, recorded.json(), "confirm")
    assert confirmed.json()["outcome"] == "confirm"
    for _ in range(3):  # накопление: после confirm провал может понадобиться не один
        failed = client.post("/api/candidate/tests/start", json={})
        lowered = take_test(client, failed.json(), "lower")
        if lowered.json()["outcome"] == "suggest_lower":
            break
    assert lowered.json()["outcome"] == "suggest_lower"
    assert lowered.json()["proposed_grade"] == "junior"
    assert lowered.json()["grade"] == "middle"
    first = client.post("/api/candidate/tests/start", json={"grade": "junior"})
    assert first.status_code == 201, first.text
    hundred = take_test(client, first.json(), "higher")
    assert hundred.status_code == 200, hundred.text
    assert hundred.json()["score"] == 100
    assert hundred.json()["outcome"] in {"suggest_higher", "confirm"}
    if hundred.json()["outcome"] == "suggest_higher":
        assert hundred.json()["proposed_grade"] == "middle"
        assert hundred.json()["grade"] == "middle"
    retry = client.post("/api/candidate/tests/start", json={"grade": "junior"})
    assert retry.status_code == 201, retry.text
    assert retry.json()["declared_grade"] == "junior"


def test_repeat_adjacent_after_written_change_waits_interval(client):
    profile_id = _seeker(client, "repeat-down@example.com", grade="senior")
    started = client.post("/api/candidate/tests/start", json={})
    offered = take_test(client, started.json(), "lower")
    assert offered.json()["proposed_grade"] == "middle"
    first_down = client.post("/api/candidate/tests/start", json={"grade": "middle"})
    assert first_down.status_code == 201, first_down.text
    moved = take_test(client, first_down.json(), "confirm")
    assert moved.json()["outcome"] == "confirm"
    assert moved.json()["grade"] == "middle"
    assert client.post("/api/candidate/tests/start", json={"grade": "junior"}).json()["error"] == "grade_not_offered"
    assert client.post("/api/candidate/tests/start", json={"grade": "senior"}).json()["error"] == "grade_not_offered"
    recorded = client.post("/api/candidate/tests/start", json={})
    lowered = take_test(client, recorded.json(), "lower")
    assert lowered.json()["proposed_grade"] == "junior"
    blocked_down = client.post("/api/candidate/tests/start", json={"grade": "junior"})
    assert blocked_down.status_code == 409
    assert blocked_down.json()["error"] == "grade_interval"
    # Ответы прошлых попыток учитываются (накопление), поэтому после провала
    # нужна не одна сильная попытка, чтобы тест предложил грейд выше.
    for _ in range(5):
        higher = client.post("/api/candidate/tests/start", json={})
        offered_up = take_test(client, higher.json(), "higher")
        if offered_up.json()["proposed_grade"] == "senior":
            break
    assert offered_up.json()["proposed_grade"] == "senior"
    blocked_up = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert blocked_up.status_code == 409
    assert blocked_up.json()["error"] == "grade_interval"
    _set_stamp(client, profile_id, "2000-01-01T00:00:00Z")
    opened = client.post("/api/candidate/tests/start", json={"grade": "senior"})
    assert opened.status_code == 201, opened.text
    _set_stamp(client, profile_id, utc_now())
    late = take_test(client, opened.json(), "confirm")
    assert late.status_code == 409
    assert late.json()["error"] == "grade_interval"
    assert _status(client, opened.json()["attempt_id"]) == "draft"


def test_page_first_lower_offer_has_no_interval_notice(client):
    _seeker(client, "html-down@example.com")
    started = client.post("/api/candidate/tests/start", json={})
    failed = take_test(client, started.json(), "lower")
    assert failed.json()["proposed_grade"] == "junior"
    page = client.get("/candidate/test")
    assert page.status_code == 200
    assert "Начать тест грейда junior" in page.text
    assert "Что случилось" not in page.text
    assert "grade_interval" not in page.text

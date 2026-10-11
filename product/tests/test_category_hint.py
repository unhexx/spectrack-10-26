"""Подсказка категории по тексту потребности."""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

from product.app.db import models as db_models
from product.app.db.session import open_session
from product.app.services.categories import latest_assignment
from product.app.services.category_hint import (
    GRADES,
    SPECIALIZATIONS,
    _GRADES,
    category_hint,
    spare_grade,
)
from product.app.services.clock import utc_now
from product.matching.rules import load_rules
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


def _seeker(client, email: str, **extra) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json={**_PROFILE, **extra})
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _login(client, email: str) -> None:
    logged = client.post("/api/auth/login", json={"email": email, "password": "password-ok"})
    assert logged.status_code == 200, logged.text


def _hint(q: str) -> list[dict]:
    db = open_session()
    try:
        return category_hint(db, q)
    finally:
        db.close()


def _pairs(q: str) -> list[tuple[str, bool]]:
    return [(row["category_id"], row["spare"]) for row in _hint(q)]


def test_spelling_tables_match_rules():
    rules = load_rules()
    assert list(SPECIALIZATIONS) == rules["specializations"]
    assert list(GRADES) == rules["grades"]
    assert list(_GRADES) == rules["grades"]
    raw = json.dumps(rules, ensure_ascii=False)
    assert "бэкенд" not in raw
    assert "джуниор" not in raw
    assert "фронтенд" not in raw


def test_spare_grade_steps_down_except_junior():
    assert spare_grade("junior") == "middle"
    assert spare_grade("middle") == "junior"
    assert spare_grade("senior") == "middle"
    assert spare_grade("lead") == "senior"


def test_hint_module_does_not_import_need_or_stemmer():
    path = Path(category_hint.__code__.co_filename)
    source = path.read_text(encoding="utf-8")
    assert "evaluate_need" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            assert not module_name.startswith("product.matching.need")
            assert not module_name.startswith("product.matching.stems")
            assert "stemmer" not in module_name
            assert "evaluate_need" not in {alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "stemmer" not in alias.name
                assert not alias.name.startswith("product.matching.need")


def test_closed_spellings_and_grade_choice(client):
    assert _hint("") == []
    assert _hint("   \n") == []
    assert _hint("бэкенде senior") == []
    assert _hint("senior") == []
    assert _hint("ведущий мидл") == []
    assert _hint("бэкенд-разработчик") == []
    assert _pairs("фронт") == [("frontend:middle", False), ("frontend:junior", True)]
    assert _pairs("бекенда") == [("backend:middle", False), ("backend:junior", True)]
    assert _pairs("«Бэкенд», senior.") == [("backend:senior", False), ("backend:middle", True)]
    assert _pairs("мобильный джун") == [("mobile:junior", False), ("mobile:middle", True)]
    assert _pairs("данные мидл и старший и джуниор") == [
        ("data:senior", False),
        ("data:middle", True),
    ]
    # Порядок специализаций — из правил, не из текста: девопс назван первым.
    assert _pairs("девопс и фронт и безопасность лид джун") == [
        ("frontend:lead", False),
        ("frontend:senior", True),
        ("security:lead", False),
        ("security:senior", True),
        ("devops:lead", False),
        ("devops:senior", True),
    ]


def test_shortlist_renders_hint_without_touching_the_form(client):
    register_and_confirm(client, email="hint-boss@example.com", role="employer")
    params = {"q": "бэкенд senior"}
    fragment = client.get("/employer/shortlist", params=params, headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "Категория backend:senior, опубликовано 0." in fragment.text
    assert "Запасная: backend:middle, опубликовано 0." in fragment.text
    assert "<form" not in fragment.text
    assert "<select" not in fragment.text
    assert "<input" not in fragment.text
    assert 'name="category_id"' not in fragment.text
    assert "вся база" not in fragment.text
    page = client.get("/employer/shortlist", params=params)
    assert page.status_code == 200
    assert "Категория backend:senior, опубликовано 0." in page.text
    assert "Запасная: backend:middle, опубликовано 0." in page.text
    blank = client.get("/employer/shortlist", params={"q": ""}, headers={"HX-Request": "true"})
    assert "Категория " not in blank.text
    assert "Запасная:" not in blank.text
    untouched = client.get("/employer/shortlist")
    assert "Запасная:" not in untouched.text
    assert "Категория " not in untouched.text
    chosen = client.get(
        "/employer/shortlist",
        params={"q": "бэкенд senior", "category_id": "data:junior"},
        headers={"HX-Request": "true"},
    )
    assert "Подборка data:junior" in chosen.text
    assert "Категория backend:senior, опубликовано 0." in chosen.text
    assert "Запасная: backend:middle, опубликовано 0." in chosen.text
    assert 'name="category_id"' not in chosen.text
    assert "<select" not in chosen.text
    assert "selected" not in chosen.text


def _categories(client, profile_id: int) -> list[str]:
    with sqlite3.connect(client.db_path) as db:
        return [
            row[0]
            for row in db.execute(
                "select category_id from category_assignments where profile_id = ? order by id",
                (profile_id,),
            )
        ]


def test_count_uses_latest_published_assignment(client):
    moved = _seeker(client, "moved-hint@example.com", grade="senior")
    updated = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "grade": "senior", "specialization": "frontend"},
    )
    assert updated.status_code == 200, updated.text
    hidden = _seeker(client, "hidden-hint@example.com", grade="senior", publication=False)
    flags = _seeker(
        client,
        "flags-hint@example.com",
        grade="senior",
        show_name=False,
        show_experience=False,
        show_achievements=False,
    )
    _seeker(client, "middle-hint@example.com", grade="middle")
    kept = _seeker(client, "kept-hint@example.com", grade="senior")
    with sqlite3.connect(client.db_path) as db:
        flags_consent = db.execute(
            "select publication, show_name, show_experience, show_achievements "
            "from consents where profile_id = ?",
            (flags,),
        ).fetchone()
        hidden_publication = db.execute(
            "select publication from consents where profile_id = ?",
            (hidden,),
        ).fetchone()[0]
    assert flags_consent == (1, 0, 0, 0)
    assert hidden_publication == 0
    assert _categories(client, moved) == ["backend:senior", "frontend:senior"]
    assert _categories(client, hidden) == ["backend:senior"]

    db = open_session()
    try:
        db.add(
            db_models.CategoryAssignment(
                profile_id=kept,
                category_id="data:lead",
                rule_version="v2",
                explanation="чужая версия",
                test_score=None,
                rank_key="0-000-0000-0-00000001",
                scored_at=utc_now(),
            )
        )
        db.commit()
        assert latest_assignment(db, kept).category_id == "backend:senior"
        assert latest_assignment(db, moved).category_id == "frontend:senior"
    finally:
        db.close()

    register_and_confirm(client, email="count-boss@example.com", role="employer")
    expected = [
        {"category_id": "backend:senior", "spare": False, "count": 2},
        {"category_id": "backend:middle", "spare": True, "count": 1},
    ]
    plain = client.get("/api/employer/category-hint", params={"q": "бэкенд senior"})
    assert plain.status_code == 200
    assert plain.json() == expected
    assert _hint("бэкенд senior") == expected
    noisy = client.get(
        "/api/employer/category-hint",
        params={
            "q": "бэкенд senior",
            "stack": "go",
            "team_role": "тимлид",
            "work_format": "офис",
            "has_fsp": "true",
            "category_id": "frontend:junior",
            "specialization": "frontend",
            "grade": "junior",
        },
    )
    assert noisy.json() == expected
    wide = client.get(
        "/employer/shortlist",
        params={"q": "бэкенд senior"},
        headers={"HX-Request": "true"},
    )
    narrow = client.get(
        "/employer/shortlist",
        params={
            "q": "бэкенд senior",
            "stack": "go",
            "team_role": "тимлид",
            "work_format": "офис",
        },
        headers={"HX-Request": "true"},
    )
    for body in (wide.text, narrow.text):
        assert "Категория backend:senior, опубликовано 2." in body
        assert "Запасная: backend:middle, опубликовано 1." in body

    _login(client, "flags-hint@example.com")
    shown = client.put(
        "/api/candidate/profile",
        json={
            **_PROFILE,
            "grade": "senior",
            "show_name": True,
            "show_experience": True,
            "show_achievements": True,
        },
    )
    assert shown.status_code == 200, shown.text
    assert _categories(client, flags) == ["backend:senior", "backend:senior"]
    _login(client, "count-boss@example.com")
    assert client.get("/api/employer/category-hint", params={"q": "бэкенд senior"}).json() == expected

    _login(client, "flags-hint@example.com")
    closed = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "grade": "senior", "publication": False},
    )
    assert closed.status_code == 200, closed.text
    _login(client, "count-boss@example.com")
    after_hide = client.get("/api/employer/category-hint", params={"q": "бэкенд senior"}).json()
    assert after_hide[0]["count"] == 1
    assert after_hide[1]["count"] == 1

    _login(client, "hidden-hint@example.com")
    opened = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "grade": "senior", "publication": True},
    )
    assert opened.status_code == 200, opened.text
    _login(client, "count-boss@example.com")
    reopened = client.get("/api/employer/category-hint", params={"q": "бэкенд senior"}).json()
    assert reopened[0]["count"] == 2
    assert reopened[1]["count"] == 1


def test_api_auth_matches_candidates_and_empty_q_is_empty(client):
    hint = client.get("/api/employer/category-hint", params={"q": "бэкенд"})
    listed = client.get("/api/employer/candidates")
    assert hint.status_code == 401
    assert listed.status_code == 401
    assert hint.json()["error"] == listed.json()["error"] == "unauthorized"

    register_and_confirm(client, email="seeker-hint@example.com", role="candidate")
    hint = client.get("/api/employer/category-hint", params={"q": "бэкенд"})
    listed = client.get("/api/employer/candidates")
    assert hint.status_code == 403
    assert listed.status_code == 403
    assert hint.json() == listed.json()

    register_and_confirm(client, email="boss-hint@example.com", role="employer")
    empty = client.get("/api/employer/category-hint")
    blank = client.get("/api/employer/category-hint", params={"q": "  "})
    assert empty.status_code == 200
    assert empty.json() == []
    assert blank.json() == []
    same = client.get("/api/employer/category-hint", params={"q": "фронт"})
    assert same.status_code == 200
    assert same.json() == _hint("фронт")

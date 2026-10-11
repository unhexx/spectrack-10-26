"""Общий стенд тестов: временный SQLite, секрет и клиент.

Новые фикстуры срезов сюда не добавлять.

ТЗ §3.5 «Качество кода»: тесты идут на временной базе, без стенда.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if len(os.environ.get("PRODUCT_SECRET", "")) < 32:
    os.environ["PRODUCT_SECRET"] = "test-secret-32-characters-minimum!!"
os.environ["PRODUCT_COOKIE_SECURE"] = "0"

import pytest
from fastapi.testclient import TestClient

from product.app.db.session import reset_engine
from product.seed import init_db


def pytest_sessionfinish(session, exitstatus):
    from product.tests.badge import write_session_badge

    write_session_badge(session, exitstatus)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setenv("PRODUCT_SQLITE", str(db_path))
    # Сборка варианта в продукте берёт случайность из secrets; в тестах — из
    # счётчика, чтобы прогон был воспроизводимым.
    import itertools
    import random

    from product.matching import assessment

    seeds = itertools.count(1)
    monkeypatch.setattr(assessment, "_rng", lambda: random.Random(next(seeds)))
    reset_engine()
    init_db()
    from product.app.main import app

    app.openapi_schema = None
    with TestClient(app) as test_client:
        test_client.db_path = db_path
        yield test_client
    reset_engine()


def register_and_confirm(
    client: TestClient,
    email: str = "seeker@example.com",
    password: str = "password-ok",
    role: str = "candidate",
    consent: bool = True,
):
    body: dict = {"email": email, "password": password, "role": role}
    if role == "candidate":
        body["consent"] = consent
    created = client.post("/api/auth/register", json=body)
    assert created.status_code == 201, created.text
    confirmed = client.post(
        "/api/auth/confirm",
        json={"email": email, "code": created.json()["code"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    logged = client.post("/api/auth/login", json={"email": email, "password": password})
    assert logged.status_code == 200, logged.text
    return logged

# --- Механика теста: ответы по серверному ключу попытки ---------------------
# Ключ хранится только в test_attempts.form_json и в API не попадает, поэтому
# тесты читают его из базы стенда. Режимы:
#   "higher"  — все ответы верные;
#   "lower"   — верно только на вопросах уровнем ниже заявленного (провал на своём);
#   "confirm" — верно на вопросах своего уровня и ниже, неверно на уровне выше.
_LEVELS = ("junior", "middle", "senior", "lead")


def stored_form(client: TestClient, attempt_id: int) -> dict:
    import json as _json
    import sqlite3 as _sqlite3

    with _sqlite3.connect(client.db_path) as db:
        raw = db.execute("select form_json from test_attempts where id = ?", (attempt_id,)).fetchone()[0]
    return _json.loads(raw)


def answer_for(item: dict, correct: bool) -> list[str] | int:
    """judge — id, отмеченные «верно». multi/order/numeric — число-ключ."""
    if item.get("type") in ("multi", "order", "numeric"):
        if correct:
            return item["key"]
        return 0 if item["key"] != 0 else 4
    return [item["key"]] if correct else []


def answers_by_mode(client: TestClient, attempt_id: int, declared: str, mode: str) -> dict:
    form = stored_form(client, attempt_id)
    done = form.get("answers") or {}
    out = {}
    for item in form["items"]:
        if item["qid"] in done:
            continue
        if mode == "higher":
            ok = True
        elif mode == "lower":
            ok = _LEVELS.index(item["level"]) < _LEVELS.index(declared)
        else:
            ok = _LEVELS.index(item["level"]) <= _LEVELS.index(declared)
        out[item["qid"]] = answer_for(item, ok)
    return out


def take_test(client: TestClient, started: dict, mode: str):
    """Сдаёт попытку целиком (с доп. блоками) и возвращает последний ответ API."""
    attempt_id = started["attempt_id"]
    response = None
    for _ in range(6):
        response = client.post(
            f"/api/candidate/tests/{attempt_id}/submit",
            json={"answers": answers_by_mode(client, attempt_id, started["declared_grade"], mode)},
        )
        if response.status_code != 200 or response.json().get("status") != "extra":
            return response
    return response

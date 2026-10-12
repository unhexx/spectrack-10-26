"""Воспроизведение находок ревью 2026-10-12. Каждый тест ПРОХОДИТ, если проблема есть."""

from __future__ import annotations

import inspect
import io
import time

import pytest

from product.matching.assessment import normalize_answer
from product.tests.conftest import register_and_confirm, stored_form
from product.tests.test_periodic import _PROFILE, _confirm, _employer, _login, _vacancy

_PRIZE_ID = "fsp-synth-1"  # в моке: места 2, «финал», 3


def _seeker(client, email: str, **extra) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json={**_PROFILE, **extra})
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _cards(client, **params) -> list[dict]:
    got = client.get("/api/employer/candidates", params={"category_id": "backend:middle", **params})
    assert got.status_code == 200, got.text
    return got.json()


# п.1 — ФСП ID вписывается руками, без Keycloak
def test_p1_external_id_is_typed_by_hand_and_squats_the_real_owner(client):
    _seeker(client, "squatter@example.com", external_id=_PRIZE_ID)
    ach = client.get("/api/candidate/achievements").json()
    assert ach["results"], "чужие достижения достались без OIDC"

    _seeker(client, "real-owner@example.com")
    again = client.put("/api/candidate/profile", json={**_PROFILE, "external_id": _PRIZE_ID})
    assert again.status_code == 409, again.text  # настоящему участнику ID уже не достаётся


def test_p1_api_save_without_key_wipes_linked_id(client):
    _seeker(client, "wipe@example.com", external_id=_PRIZE_ID)
    body = {k: v for k, v in _PROFILE.items() if k != "external_id"}
    saved = client.put("/api/candidate/profile", json=body)
    assert saved.status_code == 200
    assert saved.json()["external_id"] is None


# п.2 — код подтверждения отдаётся любому
def test_p2_resend_hands_out_code_for_someone_elses_email(client):
    created = client.post(
        "/api/auth/register",
        json={"email": "victim@example.com", "password": "password-ok", "role": "candidate", "consent": True},
    )
    assert created.status_code == 201 and "code" in created.json()
    client.cookies.clear()  # дальше — посторонний без сессии
    code = client.post("/api/auth/confirm/resend", json={"email": "victim@example.com"}).json()["code"]
    confirmed = client.post("/api/auth/confirm", json={"email": "victim@example.com", "code": code})
    assert confirmed.status_code == 200, confirmed.text


# п.3 — скрытые достижения читаются из rank_key и фильтра has_fsp
def test_p3_hidden_achievements_leak_through_rank_key_and_filter(client):
    _seeker(client, "hidden-fsp@example.com", external_id=_PRIZE_ID, show_achievements=False)
    _employer(client)
    cards = _cards(client)
    assert len(cards) == 1
    card = cards[0]
    assert card["achievements"] == [] and card["fsp_rank"] == ""  # скрыто на карточке…
    _confirmed, _score, count, prize, _pid = card["rank_key"].split("-")
    assert int(count) > 0 and prize == "1"  # …но число и приз видны в rank_key
    assert len(_cards(client, has_fsp="true")) == 1
    assert _cards(client, has_fsp="false") == []


# п.4 — скрытый стаж ищется и печатается в причине
def test_p4_hidden_experience_is_searchable(client):
    _seeker(client, "hidden-exp@example.com", experience="kubernetes в банке", show_experience=False)
    _employer(client)
    card = _cards(client, q="kubernetes")[0]
    assert card["experience"] == ""
    assert "kubernet" in card["list_reason"]  # стем скрытого слова в выдаче


# п.5 — числовой ответ роняет обработку
@pytest.mark.parametrize("raw", ["--5", "²", "1" * 5000])
def test_p5_numeric_normalizer_raises(raw):
    with pytest.raises(ValueError):
        normalize_answer({"type": "numeric"}, raw)


def test_p5_numeric_answer_is_unhandled_through_api(client):
    _seeker(client, "numeric@example.com")
    started = client.post("/api/candidate/tests/start", json={})
    assert started.status_code in (200, 201), started.text
    attempt_id = started.json()["attempt_id"]
    form = stored_form(client, attempt_id)
    numeric = [i for i in form["items"] if i.get("type") in ("multi", "order", "numeric")]
    if not numeric:
        pytest.skip("в этом варианте нет числовых вопросов")
    answers = {i["qid"]: (i["key"] if i.get("type") in ("multi", "order", "numeric") else [i["key"]])
               for i in form["items"]}
    answers[numeric[0]["qid"]] = "--5"
    # TestClient пробрасывает необработанное исключение; в проде это 500
    with pytest.raises(ValueError):
        client.post(f"/api/candidate/tests/{attempt_id}/submit", json={"answers": answers})


# п.6 — бонус задания одним кликом выше подтверждённого теста
def test_p6_one_click_completion_outranks_confirmed_score(client):
    strong = _seeker(client, "strong@example.com")
    _confirm(strong, score=100)
    weak = _seeker(client, "weak@example.com")
    _employer(client)
    assert client.post("/api/employer/vacancies", json=_vacancy()).status_code == 201
    _login(client, "weak@example.com")
    assert client.get("/api/candidate/periodic-task").json()["vacancy_id"]
    done = client.post("/api/candidate/periodic-task")  # без тела, без ответа
    assert done.status_code == 200, done.text
    _login(client, "boss-periodic@example.com")
    order = [c["profile_id"] for c in _cards(client)]
    assert order == [weak, strong]


# п.7 — разбор PDF в event loop, без лимита страниц
def test_p7_pdf_parse_runs_on_event_loop_and_is_slow():
    from reportlab.pdfgen import canvas

    from product.app.api import resumes as api_resumes
    from product.app.web import candidate as web_candidate
    from product.matching.resume import _LIMIT, read_resume_bytes

    assert inspect.iscoroutinefunction(api_resumes.upload_resume)
    assert inspect.iscoroutinefunction(web_candidate.upload_resume_page)
    assert "run_in_threadpool" not in inspect.getsource(api_resumes)

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pageCompression=1)
    for n in range(4000):
        pdf.drawString(40, 800, f"Стек: python {n}")
        pdf.showPage()
    pdf.save()
    data = buf.getvalue()
    assert len(data) <= _LIMIT
    began = time.perf_counter()
    read_resume_bytes(data)
    spent = time.perf_counter() - began
    print(f"\nPDF {len(data)} байт, 4000 страниц: разбор {spent:.2f} с")
    assert spent > 1.0

"""Категория после опроса и выдача опубликованных профилей."""

from __future__ import annotations

import json

from product.app.db import models as db_models
from product.app.db.session import open_session
from product.app.services.categories import recompute
from product.app.services.clock import utc_now
from product.matching.category import assign_category, has_prize_place, rank_key
from product.matching.need import evaluate_need, need_tokens, neighbor_phrase
from product.matching.types import CategoryError, Need, ProfileView
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


def _view(**kwargs) -> ProfileView:
    base = dict(
        profile_id=1,
        specialization="backend",
        grade="middle",
        test_score=None,
        test_outcome=None,
        fsp_confirmed_count=0,
    )
    base.update(kwargs)
    return ProfileView(**base)


def test_category_is_spec_and_grade_without_fsp_or_industry():
    plain = assign_category(_view(), "v1")
    assert plain.category_id == "backend:middle"
    assert "/" not in plain.category_id
    assert "it" not in plain.category_id
    assert plain.test_score is None
    assert plain.explanation == (
        "Опрос: специализация backend, грейд middle. Тест ещё не сдан. Правила v1."
    )
    assert "разряд" not in plain.explanation.casefold()
    assert "резюме" not in plain.explanation.casefold()
    taken = assign_category(
        _view(test_score=0, test_outcome="suggest_lower", fsp_confirmed_count=4),
        "v1",
    )
    assert taken.category_id == plain.category_id
    assert taken.test_score == 0
    assert taken.explanation == (
        "Опрос: специализация backend, грейд middle. "
        "Тест: предложен грейд ниже, балл 0. Правила v1."
    )
    assert plain.rank_key == rank_key(0, 0, 1, confirmed=False, has_prize=False)
    assert plain.rank_key == "0-000-0000-0-00000001"
    # Провал (suggest_lower) грейд не понижает, но и не подтверждает (Q&A):
    # такая карточка стоит среди неподтверждённых.
    assert taken.rank_key == rank_key(0, 4, 1, confirmed=False, has_prize=False)
    assert taken.rank_key == "0-000-0004-0-00000001"
    passed = assign_category(_view(test_score=75, test_outcome="confirm"), "v1")
    assert passed.rank_key == "1-075-0000-0-00000001"
    try:
        assign_category(_view(), "v2")
    except CategoryError as exc:
        assert exc.code == "rule_version"
    else:
        raise AssertionError("чужая версия правил должна отказать")


def test_rank_orders_score_then_achievements_and_keeps_ties():
    rows = [
        _view(profile_id=11, test_score=80, test_outcome="confirm", fsp_confirmed_count=1),
        _view(profile_id=12, test_score=80, test_outcome="confirm", fsp_confirmed_count=5),
        _view(profile_id=13, test_score=90, test_outcome="suggest_higher", fsp_confirmed_count=0),
        _view(profile_id=14, test_score=80, test_outcome="confirm", fsp_confirmed_count=5),
    ]
    ordered = sorted((assign_category(row, "v1") for row in rows), key=lambda item: item.rank_key, reverse=True)
    assert [item.profile_id for item in ordered] == [13, 14, 12, 11]
    assert {item.category_id for item in ordered} == {"backend:middle"}


def test_has_prize_place_only_first_three():
    assert has_prize_place(["1"])
    assert has_prize_place(["финал", "2"])
    assert has_prize_place(("3",))
    assert not has_prize_place(["финал", "4"])
    assert not has_prize_place([])
    assert not has_prize_place(["unknown"])


def test_rank_key_prize_bit_breaks_count_tie():
    prize = rank_key(80, 1, 1, confirmed=True, has_prize=True)
    rest = rank_key(80, 1, 2, confirmed=True, has_prize=False)
    ordered = sorted([prize, rest], reverse=True)
    assert ordered[0] == prize
    assert prize == "1-080-0001-1-00000001"
    assert rest == "1-080-0001-0-00000002"


def test_rank_key_unconfirmed_stays_below_scored_zero():
    confirmed = rank_key(0, 0, 1, confirmed=True, has_prize=False)
    unconfirmed = rank_key(0, 9, 2, confirmed=False, has_prize=True)
    ordered = sorted([confirmed, unconfirmed], reverse=True)
    assert ordered[0] == confirmed
    prize = assign_category(_view(profile_id=3, test_score=80, test_outcome="confirm", fsp_has_prize=True), "v1")
    plain = assign_category(_view(profile_id=4, test_score=80, test_outcome="confirm", fsp_has_prize=False), "v1")
    ranked = sorted([prize, plain], key=lambda item: item.rank_key, reverse=True)
    assert [item.profile_id for item in ranked] == [3, 4]


def _seeker(client, email: str, **extra) -> int:
    register_and_confirm(client, email=email, role="candidate")
    saved = client.put("/api/candidate/profile", json={**_PROFILE, **extra})
    assert saved.status_code == 200, saved.text
    return saved.json()["profile_id"]


def _submit(profile_id: int, score: int, outcome: str, spec: str = "backend", grade: str = "middle") -> None:
    db = open_session()
    try:
        now = utc_now()
        db.add(
            db_models.TestAttempt(
                profile_id=profile_id,
                specialization=spec,
                declared_grade=grade,
                template_id="bank",
                rule_version="v1",
                answers_json="[]",
                score=score,
                outcome=outcome,
                proposed_grade=grade,
                status="submitted",
                created_at=now,
                submitted_at=now,
            )
        )
        recompute(db, profile_id)
        db.commit()
    finally:
        db.close()


def test_survey_shows_category_before_the_test(client):
    register_and_confirm(client)
    missing = client.get("/api/candidate/category")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    saved = client.put("/api/candidate/profile", json=_PROFILE)
    assert saved.status_code == 200
    body = client.get("/api/candidate/category").json()
    assert body["category_id"] == "backend:middle"
    assert body["industry"] == "it"
    assert body["rule_version"] == "v1"
    assert body["test_score"] is None
    assert body["explanation"].endswith("Тест ещё не сдан. Правила v1.")
    page = client.get("/candidate")
    assert "backend:middle" in page.text
    assert "Опрос:" in page.text


def test_employer_sees_scored_profiles_without_email(client):
    high = _seeker(client, "high@example.com", experience="платежи в контуре", stack=["python"])
    many = _seeker(client, "many@example.com", external_id="fsp-synth-1", stack=["go"])
    one = _seeker(client, "one@example.com", external_id="fsp-synth-3", stack=["python"], team_role="аналитик")
    tie_low = _seeker(client, "tie-a@example.com", stack=["sql"])
    tie_high = _seeker(client, "tie-b@example.com", stack=["sql"])
    zero = _seeker(client, "zero@example.com", stack=["python"])
    hidden = _seeker(client, "hidden@example.com", publication=False, stack=["python"])
    fresh = _seeker(client, "fresh@example.com", stack=["python"])
    _submit(high, 90, "suggest_higher")
    _submit(many, 40, "suggest_lower")
    _submit(one, 40, "suggest_lower")
    _submit(tie_low, 40, "suggest_lower")
    _submit(tie_high, 40, "suggest_lower")
    _submit(zero, 0, "suggest_lower")
    _submit(hidden, 100, "suggest_higher")

    register_and_confirm(client, email="boss@example.com", role="employer")
    denied = client.get("/api/candidate/category")
    assert denied.status_code == 403
    # ТЗ §2.1 «Поиск по банку кандидатов»: без категории — вся опубликованная база.
    naked = client.get("/api/employer/candidates")
    assert naked.status_code == 200
    whole = [card["profile_id"] for card in naked.json()]
    assert fresh in whole and high in whole and hidden not in whole
    by_spec = client.get("/api/employer/candidates", params={"specialization": "backend", "grade": "middle"})
    assert by_spec.status_code == 200
    assert {card["category_id"] for card in by_spec.json()} <= {"backend:middle"}
    wrong = client.get("/api/employer/candidates", params={"specialization": "cobol"})
    assert wrong.status_code == 422 and wrong.json()["error"] == "closed_list"
    clash = client.get(
        "/api/employer/candidates", params={"category_id": "backend:middle", "grade": "senior"}
    )
    assert clash.status_code == 422 and clash.json()["error"] == "category_id"
    bad = client.get("/api/employer/candidates", params={"category_id": "it:middle"})
    assert bad.status_code == 422
    assert bad.json()["error"] == "category_id"
    flag = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "has_fsp": "yes"},
    )
    assert flag.status_code == 422
    assert flag.json()["error"] == "closed_list"

    listed = client.get("/api/employer/candidates", params={"category_id": "backend:middle"})
    assert listed.status_code == 200
    cards = listed.json()
    ids = [card["profile_id"] for card in cards]
    assert fresh in ids
    assert hidden not in ids
    assert zero in ids
    assert ids[0] == high
    # zero провалил тест: грейд не подтверждён, как и у fresh без теста (Q&A) —
    # обе карточки в выдаче, ниже подтверждённых.
    assert ids.index(high) < ids.index(zero) and ids.index(high) < ids.index(fresh)
    assert ids.index(many) < ids.index(one)
    assert ids.index(one) < ids.index(tie_low)
    assert ids.index(one) < ids.index(tie_high)
    assert tie_high in ids and tie_low in ids
    assert ids.index(tie_high) < ids.index(tie_low)
    dumped = json.dumps(cards, ensure_ascii=False)
    assert "high@example.com" not in dumped
    assert "candidate_email" not in dumped
    for card in cards:
        assert "email" not in card
        assert card["industry"] == "it"
        assert card["category_id"] == "backend:middle"
        assert "it" not in card["category_id"]
    many_card = next(card for card in cards if card["profile_id"] == many)
    assert many_card["fsp_rank"] == "I"
    assert len(many_card["achievements"]) == 5
    assert "Программирование алгоритмическое" in {row["discipline"] for row in many_card["achievements"]}
    zero_card = next(card for card in cards if card["profile_id"] == zero)
    assert zero_card["test_score"] == 0
    assert zero_card["grade_status"] == "не подтверждён тестом"
    assert "балл 0" in zero_card["explanation"]
    fresh_card = next(card for card in cards if card["profile_id"] == fresh)
    assert fresh_card["test_score"] is None
    assert fresh_card["grade_status"] == "не подтверждён"
    assert fresh_card["rank_key"].startswith("0-")
    assert zero_card["rank_key"].startswith("0-")
    assert "Грейд не подтверждён, потребность без уточнений" in fresh_card["list_reason"]

    only_python = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "stack": "python"},
    )
    python_ids = {card["profile_id"] for card in only_python.json()}
    assert high in python_ids
    assert many not in python_ids
    text = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "q": "платеж"},
    )
    text_ids = [card["profile_id"] for card in text.json()]
    assert text_ids[0] == high
    assert set(text_ids) == set(ids)
    reason = text.json()[0]["list_reason"]
    assert "платеж" in reason
    assert "в тексте профиля" in reason
    assert "Текст: платеж" not in reason
    empty_reason = cards[0]["list_reason"]
    assert empty_reason.startswith("Категория backend:middle")
    assert "Тест сдан, потребность без уточнений" in empty_reason
    role = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "team_role": "аналитик"},
    )
    assert [card["profile_id"] for card in role.json()] == [one]
    with_fsp = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "has_fsp": "true"},
    )
    assert {card["profile_id"] for card in with_fsp.json()} == {many, one}
    without = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "has_fsp": "false"},
    )
    assert many not in {card["profile_id"] for card in without.json()}
    odd = client.get(
        "/api/employer/candidates",
        params={"category_id": "backend:middle", "q": "https://jobs.example/vacancy"},
    )
    assert odd.status_code == 200
    assert [card["profile_id"] for card in odd.json()] == ids

    overlap_low = _seeker(client, "overlap-low@example.com", stack=["python", "sql"])
    overlap_high_id = _seeker(client, "overlap-high@example.com", stack=["python"])
    _submit(overlap_low, 90, "suggest_higher")
    _submit(overlap_high_id, 90, "suggest_higher")
    logged = client.post("/api/auth/login", json={"email": "boss@example.com", "password": "password-ok"})
    assert logged.status_code == 200, logged.text
    ranked = client.get(
        "/api/employer/candidates",
        params=[("category_id", "backend:middle"), ("stack", "python"), ("stack", "sql")],
    )
    ranked_ids = [card["profile_id"] for card in ranked.json()]
    assert overlap_low in ranked_ids
    assert overlap_high_id in ranked_ids
    assert ranked_ids.index(overlap_low) < ranked_ids.index(overlap_high_id)
    assert "в профиле стек python" in ranked.json()[0]["list_reason"]
    assert "Стек: python" not in ranked.json()[0]["list_reason"]


def test_history_survives_specialization_change(client):
    profile_id = _seeker(client, "move@example.com")
    _submit(profile_id, 70, "confirm")
    moved = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "specialization": "frontend"},
    )
    assert moved.status_code == 200
    assert moved.json()["category_id"] == "frontend:middle"
    assert "Тест ещё не сдан" in moved.json()["explanation"]
    import sqlite3

    with sqlite3.connect(client.db_path) as db:
        stored = db.execute(
            "select category_id, test_score from category_assignments where profile_id = ? order by id",
            (profile_id,),
        ).fetchall()
    assert ("backend:middle", 70) in stored
    assert stored[-1] == ("frontend:middle", None)
    assert len(stored) >= 2


def test_prize_place_ranks_above_participant_with_same_count(client):
    db = open_session()
    try:
        db.add(db_models.FspParticipant(external_id="fsp-prize-1", display_name="Призёр", rank="I"))
        db.add(db_models.FspParticipant(external_id="fsp-prize-4", display_name="Участник", rank="I"))
        db.flush()
        db.add(
            db_models.FspAchievement(
                external_id="fsp-prize-1",
                competition_id="prize-1",
                competition_name="Синтетический кубок",
                discipline="алгоритмика",
                role_in_team="участник",
                place="1",
                held_on="2025-04-12",
            )
        )
        db.add(
            db_models.FspAchievement(
                external_id="fsp-prize-4",
                competition_id="prize-4",
                competition_name="Синтетический кубок",
                discipline="алгоритмика",
                role_in_team="участник",
                place="4",
                held_on="2025-04-12",
            )
        )
        db.commit()
    finally:
        db.close()
    prize = _seeker(client, "prize@example.com", external_id="fsp-prize-1", stack=["python"])
    rest = _seeker(client, "rest@example.com", external_id="fsp-prize-4", stack=["python"])
    _submit(prize, 80, "confirm")
    _submit(rest, 80, "confirm")
    register_and_confirm(client, email="boss-prize@example.com", role="employer")
    listed = client.get("/api/employer/candidates", params={"category_id": "backend:middle"})
    ids = [card["profile_id"] for card in listed.json()]
    assert ids.index(prize) < ids.index(rest)
    prize_card = next(card for card in listed.json() if card["profile_id"] == prize)
    rest_card = next(card for card in listed.json() if card["profile_id"] == rest)
    assert prize_card["rank_key"].split("-")[3] == "1"
    assert rest_card["rank_key"].split("-")[3] == "0"


def test_need_ranks_stack_overlap_then_rank_key():
    empty = Need(category_id="backend:middle")
    both = Need(category_id="backend:middle", stack=("python", "sql"))
    common = dict(
        assigned_category_id="backend:middle",
        test_score=90,
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=0,
        rank_key="1-010-0000-0-00000001",
    )
    python_only = evaluate_need(
        stack=["python"],
        haystack="backend middle python разработчик удалённо",
        need=both,
        **common,
    )
    python_sql = evaluate_need(
        stack=["python", "sql"],
        haystack="backend middle python sql разработчик удалённо",
        need=both,
        **common,
    )
    assert python_sql.sort_key > python_only.sort_key
    vacant = evaluate_need(
        stack=["python"],
        haystack="backend middle python разработчик удалённо",
        need=empty,
        fsp_count=9,
        assigned_category_id="backend:middle",
        test_score=90,
        team_role="разработчик",
        work_format="удалённо",
        rank_key="1-010-0009-0-00000001",
    )
    assert vacant.sort_key[:5] == (0, 0, 0, 0, 0)
    high_key = "1-090-0000-0-00000002"
    low_key = "1-010-0009-0-00000001"
    ordered = sorted(
        [high_key, low_key],
        key=lambda key: (0, 0, 0, 0, 0, key),
        reverse=True,
    )
    assert ordered[0] == high_key
    reason = evaluate_need(
        assigned_category_id="backend:middle",
        test_score=90,
        stack=["python"],
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=0,
        haystack="backend middle python разработчик удалённо платежи в контуре",
        need=Need(category_id="backend:middle", q="платеж"),
    ).reason
    assert "в тексте профиля: платеж" in reason
    assert "Стек:" not in reason
    assert "Текст: платеж" not in reason
    vacant_unconfirmed = evaluate_need(
        assigned_category_id="backend:middle",
        test_score=None,
        stack=["python"],
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=0,
        haystack="backend middle python разработчик удалённо",
        need=Need(category_id="backend:middle"),
        rank_key="0-000-0000-0-00000001",
    )
    assert vacant_unconfirmed.matched
    assert vacant_unconfirmed.reason == "Категория backend:middle. Грейд не подтверждён, потребность без уточнений."
    vacant_scored = evaluate_need(
        assigned_category_id="backend:middle",
        test_score=67,
        stack=["python"],
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=0,
        haystack="backend middle python разработчик удалённо",
        need=Need(category_id="backend:middle"),
        rank_key="1-067-0000-0-00000001",
    )
    assert vacant_scored.reason == "Категория backend:middle. Тест сдан, потребность без уточнений."


def test_neighbor_phrase_names_the_rank_field_that_puts_the_card_above():
    score = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-067-0000-0-00000002"),
        (0, 0, 0, 0, 0, "1-033-0009-1-00000001"),
    )
    assert score == "Выше соседней карточки по полю балл: 67 против 33."
    assert neighbor_phrase((0, 0, 0, 0, 0, "1-033-0009-1-00000001"), None) == (
        "Ниже этой карточки в подборке никого нет."
    )
    confirmed = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-000-0000-0-00000002"),
        (0, 0, 0, 0, 0, "0-000-0009-1-00000001"),
    )
    assert confirmed == "Выше соседней карточки по полю подтверждён: да против нет."
    prize = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-080-0001-1-00000002"),
        (0, 0, 0, 0, 0, "1-080-0001-0-00000003"),
    )
    assert prize == "Выше соседней карточки по полю приз: да против нет."
    count = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-040-0005-0-00000002"),
        (0, 0, 0, 0, 0, "1-040-0001-0-00000003"),
    )
    assert count == "Выше соседней карточки по полю число достижений: 5 против 1."
    tied = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-040-0001-0-00000009"),
        (0, 0, 0, 0, 0, "1-040-0001-0-00000003"),
    )
    assert tied == "С соседней карточкой совпадают подтверждён, балл, число достижений и приз."
    hidden = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-040-0005-0-00000002"),
        (0, 0, 0, 0, 0, "1-040-0001-0-00000003"),
        mention_achievements=False,
    )
    assert hidden == "Выше соседней карточки."
    assert "достижен" not in hidden
    assert "5" not in hidden
    score_only = neighbor_phrase(
        (0, 0, 0, 0, 0, "1-067-0005-1-00000002"),
        (0, 0, 0, 0, 0, "1-033-0001-0-00000001"),
        mention_achievements=False,
    )
    assert score_only == "Выше соседней карточки по полю балл: 67 против 33."
    assert "достижен" not in score_only


def test_neighbor_phrase_does_not_pretend_rank_when_need_is_ahead():
    text = neighbor_phrase(
        (2, 0, 0, 0, 0, "1-040-0000-0-00000002"),
        (1, 0, 0, 0, 0, "1-090-0000-0-00000001"),
    )
    assert text.startswith("Выше соседней карточки по совпадению потребности:")
    assert "подтверждён да против да" in text
    assert "балл 40 против 90" in text
    assert "число достижений 0 против 0" in text
    assert "приз нет против нет" in text
    untouched = evaluate_need(
        assigned_category_id="backend:middle",
        test_score=40,
        stack=["python", "sql"],
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=0,
        haystack="backend middle python sql разработчик удалённо",
        need=Need(category_id="backend:middle", stack=("python", "sql")),
        rank_key="1-040-0000-0-00000002",
    )
    assert untouched.sort_key == (2, 0, 0, 0, 0, "1-040-0000-0-00000002")
    assert "Выше соседней" not in untouched.reason
    quiet = evaluate_need(
        assigned_category_id="backend:middle",
        test_score=40,
        stack=["python"],
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=5,
        haystack="backend middle python",
        need=Need(category_id="backend:middle", has_fsp=True),
        rank_key="1-040-0005-0-00000002",
        mention_achievements=False,
    )
    assert quiet.matched
    assert quiet.sort_key[3] == 1
    assert "достижен" not in quiet.reason
    spoken = evaluate_need(
        assigned_category_id="backend:middle",
        test_score=40,
        stack=["python"],
        team_role="разработчик",
        work_format="удалённо",
        fsp_count=5,
        haystack="backend middle python",
        need=Need(category_id="backend:middle", has_fsp=True),
        rank_key="1-040-0005-0-00000002",
    )
    assert "в профиле 5 достижений ФСП" in spoken.reason


def test_snowballstemmer_module_is_pure_python():
    from snowballstemmer import stemmer

    assert type(stemmer("russian")).__module__ == "snowballstemmer.russian_stemmer"
    assert type(stemmer("english")).__module__ == "snowballstemmer.english_stemmer"


def test_need_matches_stems_not_substrings():
    def fit(q: str, haystack: str):
        return evaluate_need(
            assigned_category_id="backend:middle",
            test_score=90,
            stack=["python"],
            team_role="разработчик",
            work_format="удалённо",
            fsp_count=0,
            haystack=haystack,
            need=Need(category_id="backend:middle", q=q),
            rank_key="1-090-0000-0-00000001",
        )

    same = fit("данных", "данными")
    assert same.matched
    assert same.sort_key == (0, 0, 0, 0, 1, "1-090-0000-0-00000001")
    assert "в тексте профиля: дан" in same.reason
    for query, haystack in (
        ("дан", "задание"),
        ("дан", "издание"),
        ("разработка", "разработчик"),
        ("бэкенд", "backend"),
    ):
        missed = fit(query, haystack)
        assert missed.matched
        assert missed.sort_key[4] == 0
    empty = fit("", "данными")
    assert empty.matched
    assert empty.sort_key == (0, 0, 0, 0, 0, "1-090-0000-0-00000001")
    short = fit("go js", "golang javascript")
    assert short.matched
    assert short.sort_key[4] == 0
    kept = fit("go js", "go js")
    assert kept.sort_key[4] == 2
    assert kept.sort_key > short.sort_key
    partial = fit("данных очередей", "данными")
    assert partial.matched
    assert partial.sort_key[4] == 1
    full = fit("данных очередей", "данными очередями")
    assert full.sort_key[4] == 2
    assert full.sort_key > partial.sort_key


def test_need_tokens_do_not_share_a_stemmer_between_threads():
    import threading

    barrier = threading.Barrier(2)
    found: dict[str, list[str]] = {}

    def run(key: str, word: str) -> None:
        barrier.wait()
        found[key] = need_tokens(word)

    threads = [
        threading.Thread(target=run, args=("ru", "данных")),
        threading.Thread(target=run, args=("en", "developers")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found["ru"] == ["дан"]
    assert found["en"] == ["develop"]


def test_recompute_does_not_read_visibility_flags():
    import inspect

    source = inspect.getsource(recompute)
    assert "show_" not in source


def test_ensure_columns_adds_visibility_not_null_default(tmp_path):
    from sqlalchemy import create_engine

    from product.app.db.session import ensure_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            create table consents (
                id integer primary key,
                profile_id integer not null,
                processing integer not null,
                publication integer not null,
                accepted_at text not null
            )
            """
        )
        conn.exec_driver_sql(
            "insert into consents (profile_id, processing, publication, accepted_at) "
            "values (1, 1, 1, '2020-01-01T00:00:00Z')"
        )
    ensure_columns(engine)
    ensure_columns(engine)
    with engine.begin() as conn:
        info = conn.exec_driver_sql("PRAGMA table_info(consents)").fetchall()
        old = conn.exec_driver_sql(
            "select show_name, show_experience, show_achievements from consents where profile_id = 1"
        ).one()
        conn.exec_driver_sql(
            "insert into consents (profile_id, processing, publication, accepted_at) "
            "values (2, 0, 0, '2020-01-02T00:00:00Z')"
        )
        fresh = conn.exec_driver_sql(
            "select show_name, show_experience, show_achievements from consents where profile_id = 2"
        ).one()
    by_name = {row[1]: row for row in info}
    for name in ("show_name", "show_experience", "show_achievements"):
        column = by_name[name]
        assert column[2].upper() == "INTEGER"
        assert column[3] == 1
        assert str(column[4]).strip("'") == "1"
    assert tuple(old) == (1, 1, 1)
    assert tuple(fresh) == (1, 1, 1)
    assert None not in old and None not in fresh


def test_visibility_flags_mask_the_employer_card(client):
    from product.fsp_mock.adapter import CANONICAL_DISCIPLINES

    open_name = "Тайноеимя Сокрытов"
    experience = "платежи в контуре"
    carrier = "Носитель Достижений"
    single = "Носитель Одного"

    def login(email: str) -> None:
        logged = client.post("/api/auth/login", json={"email": email, "password": "password-ok"})
        assert logged.status_code == 200, logged.text

    def cards(params: dict) -> list[dict]:
        listed = client.get("/api/employer/candidates", params=params)
        assert listed.status_code == 200, listed.text
        return listed.json()

    visible_id = _seeker(
        client,
        "flags-a@example.com",
        full_name=open_name,
        experience=experience,
    )
    many_id = _seeker(
        client,
        "flags-b@example.com",
        full_name=carrier,
        external_id="fsp-synth-1",
    )
    rank_before = client.get("/api/candidate/category").json()["rank_key"]
    one_id = _seeker(
        client,
        "flags-c@example.com",
        full_name=single,
        external_id="fsp-synth-3",
        show_achievements=False,
    )
    register_and_confirm(client, email="flags-boss@example.com", role="employer")
    opened = cards({"category_id": "backend:middle"})
    visible = next(card for card in opened if card["profile_id"] == visible_id)
    many = next(card for card in opened if card["profile_id"] == many_id)
    assert visible["full_name"] == open_name
    assert visible["experience"] == experience
    assert many["fsp_rank"] == "I"
    assert len(many["achievements"]) == 5
    assert "Программирование алгоритмическое" in {row["discipline"] for row in many["achievements"]}
    page = client.get("/employer/shortlist", params={"category_id": "backend:middle"})
    assert open_name in page.text
    assert "Программирование алгоритмическое" in page.text

    login("flags-a@example.com")
    hidden_name = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "full_name": open_name, "experience": experience, "show_name": False},
    )
    assert hidden_name.status_code == 200, hidden_name.text
    own = client.get("/api/candidate/profile").json()
    assert own["full_name"] == open_name
    assert own["experience"] == experience
    assert own["show_name"] is False
    login("flags-boss@example.com")
    masked = next(card for card in cards({"category_id": "backend:middle"}) if card["profile_id"] == visible_id)
    assert masked["full_name"] == ""
    page = client.get("/employer/shortlist", params={"category_id": "backend:middle"})
    assert open_name not in page.text
    assert f"Номер профиля: {visible_id}." in page.text

    login("flags-a@example.com")
    hidden_experience = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "full_name": open_name, "experience": experience, "show_experience": False},
    )
    assert hidden_experience.status_code == 200, hidden_experience.text
    login("flags-boss@example.com")
    found = cards({"category_id": "backend:middle", "q": "платеж"})
    assert found[0]["profile_id"] == visible_id
    assert found[0]["experience"] == ""
    assert experience not in found[0]["list_reason"]
    assert "платеж" in found[0]["list_reason"]
    quiet = client.get("/employer/shortlist", params={"category_id": "backend:middle", "q": "платеж"})
    assert experience not in quiet.text

    login("flags-b@example.com")
    hidden_score = client.put(
        "/api/candidate/profile",
        json={
            **_PROFILE,
            "full_name": carrier,
            "external_id": "fsp-synth-1",
            "show_achievements": False,
        },
    )
    assert hidden_score.status_code == 200, hidden_score.text
    rank_after = client.get("/api/candidate/category").json()["rank_key"]
    assert rank_after == rank_before
    login("flags-boss@example.com")
    hidden_cards = cards({"category_id": "backend:middle"})
    many = next(card for card in hidden_cards if card["profile_id"] == many_id)
    one = next(card for card in hidden_cards if card["profile_id"] == one_id)
    assert many["rank_key"] == rank_before
    for card in (many, one):
        assert card["achievements"] == []
        assert card["fsp_rank"] == ""
        assert "достижен" not in card["list_reason"]
        assert "5" not in card["list_reason"]
    assert "1" not in one["list_reason"]
    with_fsp = {card["profile_id"] for card in cards({"category_id": "backend:middle", "has_fsp": "true"})}
    without_fsp = {card["profile_id"] for card in cards({"category_id": "backend:middle", "has_fsp": "false"})}
    assert many_id in with_fsp and one_id in with_fsp
    assert many_id not in without_fsp and one_id not in without_fsp
    for card in cards({"category_id": "backend:middle", "has_fsp": "true"}):
        if card["profile_id"] in (many_id, one_id):
            assert "достижен" not in card["list_reason"]
            assert "5" not in card["list_reason"]
    page = client.get("/employer/shortlist", params={"category_id": "backend:middle"})
    for discipline in CANONICAL_DISCIPLINES:
        assert discipline not in page.text
    assert "Квест выходного дня" not in page.text
    assert "Достижений ФСП нет." not in page.text
    assert "Разряд источника" not in page.text

    login("flags-a@example.com")
    unpublished = client.put(
        "/api/candidate/profile",
        json={**_PROFILE, "full_name": open_name, "experience": experience, "publication": False},
    )
    assert unpublished.status_code == 200, unpublished.text
    login("flags-boss@example.com")
    left = {card["profile_id"] for card in cards({"category_id": "backend:middle"})}
    assert visible_id not in left
    assert many_id in left

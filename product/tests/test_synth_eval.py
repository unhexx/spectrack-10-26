"""Контракт SYNTH-EVAL: имена, слоты и сборщик шарда."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import product
from product.matching.stems import catalog
from product.matching.synth_eval import (
    FIRST_FEMALE,
    FIRST_MALE,
    N,
    SEED,
    SHARD_SIZE,
    SURNAMES,
    answers_for,
    as_cases,
    assign_slot,
    build_shard,
    extras_for_shard,
    forbidden_name,
    generate_chosen,
    load_shards,
    load_template,
    profile_id_for,
)
from product.matching import synth_eval, synth_eval_chart, synth_eval_run

_NAMES = Path(product.__file__).resolve().parent / "matching" / "golden" / "synth_eval" / "names.json"

_GOLDEN_SLOTS = {
    0: {
        "shard": 1,
        "offset": 0,
        "t": 0,
        "k": 0,
        "template_id": "backend-junior-a",
        "claim_kind": "aligned",
        "persona": "competent",
        "true_grade": "junior",
    },
    216: {
        "shard": 1,
        "offset": 216,
        "t": 0,
        "k": 3,
        "template_id": "backend-junior-a",
        "claim_kind": "aligned",
        "persona": "competent",
        "true_grade": "junior",
    },
    249: {
        "shard": 1,
        "offset": 249,
        "t": 68,
        "k": 3,
        "template_id": "security-middle-c",
        "claim_kind": "stretch",
        "persona": "adjacent",
        "true_grade": "junior",
    },
    250: {
        "shard": 2,
        "offset": 0,
        "t": 0,
        "k": 0,
        "template_id": "backend-junior-a",
        "claim_kind": "aligned",
        "persona": "competent",
        "true_grade": "junior",
    },
    999: {
        "shard": 4,
        "offset": 249,
        "t": 71,
        "k": 3,
        "template_id": "security-senior-c",
        "claim_kind": "stretch",
        "persona": "adjacent",
        "true_grade": "middle",
    },
}


def _names() -> dict:
    return json.loads(_NAMES.read_text(encoding="utf-8"))


def test_names_json_has_unique_living_chosen():
    data = _names()
    assert data["seed"] == SEED
    assert data["first_male"] == FIRST_MALE
    assert data["first_female"] == FIRST_FEMALE
    assert data["surnames"] == SURNAMES
    assert len(data["first_male"]) == 40
    assert len(data["first_female"]) == 40
    assert len(data["surnames"]["m"]) == 40
    assert len(data["surnames"]["f"]) == 40
    chosen = data["chosen"]
    assert len(chosen) == 1000
    assert len(set(chosen)) == 1000
    assert all(isinstance(name, str) and " " in name for name in chosen)
    mocks = {
        "Иван Синтетиков",
        "Анна Синтетикова",
        "Мария Черновикова",
        "Пётр Черновой",
    }
    for name in chosen:
        assert not forbidden_name(name)
        assert name not in mocks


def test_generate_chosen_is_deterministic():
    data = _names()
    again = generate_chosen(data["first_male"], data["first_female"], data["surnames"], seed=data["seed"])
    assert again == data["chosen"]
    from_module = generate_chosen(FIRST_MALE, FIRST_FEMALE, SURNAMES, seed=SEED)
    assert from_module == data["chosen"]


def test_assign_slot_golden_tuples():
    for index, expected in _GOLDEN_SLOTS.items():
        slot = assign_slot(index)
        assert slot["index"] == index
        for key, value in expected.items():
            assert slot[key] == value, (index, key, slot[key], value)
    assert assign_slot(0)["t"] == 0
    assert assign_slot(999)["t"] == 71


def test_extras_for_shard_closed_form():
    assert extras_for_shard(1)[-1] == (68, 3)
    assert extras_for_shard(4)[-1] == (71, 3)
    for shard in range(1, 5):
        extras = extras_for_shard(shard)
        assert len(extras) == 34
        assert extras == sorted(extras)


def test_claim_kind_counts_on_thousand():
    kinds: dict[str, int] = {}
    personas: dict[str, int] = {}
    for index in range(N):
        slot = assign_slot(index)
        assert slot["k"] in {0, 1, 2, 3, 4}
        kinds[slot["claim_kind"]] = kinds.get(slot["claim_kind"], 0) + 1
        personas[slot["persona"]] = personas.get(slot["persona"], 0) + 1
    assert kinds == {"aligned": 898, "stretch": 54, "underclaim": 48}
    assert personas == {"competent": 642, "confirm": 304, "adjacent": 54}


def test_twenty_four_pairs_cover_letters():
    letters: dict[tuple[str, str], set[str]] = {}
    for index in range(N):
        slot = assign_slot(index)
        key = (slot["specialization"], slot["declared_grade"])
        letters.setdefault(key, set()).add(slot["letter"])
    assert len(letters) == 24
    for pair, seen in letters.items():
        assert seen == {"a", "b", "c"}, pair


def test_profile_id_ray_congruence():
    used_one: set[int] = set()
    pid_one = profile_id_for("backend", "junior", "a", used_one, 1)
    assert pid_one >= 10001
    assert pid_one % 4 == 1
    used_four: set[int] = set()
    pid_four = profile_id_for("backend", "junior", "a", used_four, 4)
    assert pid_four >= 10001
    assert pid_four % 4 == 0


def test_answers_for_follows_catalog_correct():
    triples = []
    for letter in "abc":
        template = load_template(f"backend-middle-{letter}")
        answers = answers_for(template, "competent", slot=0)
        assert answers == [question["correct"] for question in template["questions"]]
        triples.append(tuple(answers))
    assert len(triples) == 3
    assert all(len(item) == 3 for item in triples)


def test_adjacent_persona_picks_non_correct_letters():
    template = catalog()[0]
    answers = answers_for(template, "adjacent")
    correct = [str(question["correct"]) for question in template["questions"]]
    assert answers != correct
    assert all(answer != letter for answer, letter in zip(answers, correct))


def test_load_shards_missing_is_ok():
    bundle = load_shards()
    assert bundle["n"] == len(bundle["profiles"])
    assert bundle["n"] in {0, 250, 500, 750, 1000}


def test_build_shard_one_in_tmp(tmp_path):
    chosen = _names()["chosen"]
    shard = build_shard(1, names=chosen[:SHARD_SIZE])
    (tmp_path / "shard-1.json").write_text(
        json.dumps(shard, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert shard["n"] == 250
    assert len(shard["profiles"]) == 250
    assert len(shard["needs"]) == 3
    assert all(need["relevant_profile_ids"] == [] for need in shard["needs"])
    assert [need["id"] for need in shard["needs"]] == [
        "backend-middle",
        "frontend-middle",
        "mobile-middle",
    ]
    assert len(shard["template_pairs"]) == 6
    assert [pair["template_id"] for pair in shard["template_pairs"]] == [
        "backend-middle-a",
        "data-middle-a",
        "devops-middle-a",
        "frontend-middle-a",
        "mobile-middle-a",
        "security-middle-a",
    ]
    for pair in shard["template_pairs"]:
        assert pair["answers_repeat"] == pair["answers"]
    living = [row["display_name"] for row in shard["profiles"]]
    assert living == chosen[:SHARD_SIZE]
    for row in shard["profiles"]:
        assert row["test_score"] is None
        assert row["test_outcome"] is None
        assert row["expected_outcome"] is None
        assert row["expected_category_id"] == f"{row['specialization']}:{row['declared_grade']}"
        assert row["profile_id"] >= 10001
        assert row["profile_id"] % 4 == 1


@pytest.fixture(scope="module")
def mini_bundle() -> dict:
    chosen = _names()["chosen"]
    shard = build_shard(1, names=chosen[:SHARD_SIZE])
    return {
        "profiles": shard["profiles"][:8],
        "needs": shard["needs"][:2],
        "template_pairs": shard["template_pairs"][:1],
        "n": 8,
        "seed": shard["seed"],
        "rule_version": shard["rule_version"],
    }


def test_mini_profiles_are_living(mini_bundle):
    for row in mini_bundle["profiles"]:
        assert not forbidden_name(row["display_name"])
        assert "синтетик" not in row["display_name"].casefold()
        assert len(row["answers"]) == 3
        assert set(row["answers"]) <= {"a", "b", "c"}


def test_as_cases_shape(mini_bundle):
    cases = as_cases(mini_bundle)
    assert set(cases) == {"top_k", "profiles", "needs", "template_pairs"}
    assert cases["top_k"] == 3
    assert cases["profiles"] == mini_bundle["profiles"]
    assert cases["needs"] == mini_bundle["needs"]
    assert cases["template_pairs"] == mini_bundle["template_pairs"]


def test_chart_writes_svg_and_png(tmp_path, mini_bundle):
    svg_path, png_path = synth_eval_chart.write_chart(tmp_path, mini_bundle)
    assert svg_path == tmp_path / "synth-eval.svg"
    assert png_path == tmp_path / "synth-eval.png"
    svg = svg_path.read_text(encoding="utf-8")
    counts = synth_eval_run.collect_counts(mini_bundle)
    for name, (hit, total) in counts.items():
        assert f"{name} {hit} {total}" in svg
    assert "font-family=\"JetBrains Mono\"" in svg
    assert "#1B1C21" in svg
    assert "#402FFF" in svg
    assert "#EDEDED" in svg
    assert "#2f6f9f" not in svg
    assert png_path.is_file()
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_matching_does_not_import_matplotlib():
    root = Path(product.__file__).resolve().parent / "matching"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all(name.split(".")[0] != "matplotlib" for name in names)


def test_chart_main_incomplete_shards(monkeypatch):
    monkeypatch.setattr(
        synth_eval_chart,
        "load_shards",
        lambda: {"shards": [], "n": 0, "profiles": [], "needs": [], "template_pairs": []},
    )
    with pytest.raises(SystemExit, match="shards_incomplete"):
        synth_eval_chart.main()


def test_builder_cli_rejects_fill_relevant():
    with pytest.raises(SystemExit, match="usage:"):
        synth_eval.main(["--fill-relevant"])

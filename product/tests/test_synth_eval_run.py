"""Раннер SYNTH-EVAL: девять строк записки и разметка relevant в памяти."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import product
from product.matching.golden_run import summarize
from product.matching.synth_eval import SHARD_SIZE, as_cases, build_shard, load_shards
from product.matching.synth_eval_run import (
    annotate_relevant,
    collect_counts,
    render_synth_report,
)
from product.matching import synth_eval_run

_REPORT_NAMES = (
    "category_match",
    "outcome_match",
    "template_stable",
    "relevant_in_top",
    "resume_ok",
    "grade_separation",
    "sim_competent",
    "sim_key_leak",
    "sim_adjacent",
)

_NAMES = Path(product.__file__).resolve().parent / "matching" / "golden" / "synth_eval" / "names.json"


def _names() -> dict:
    return json.loads(_NAMES.read_text(encoding="utf-8"))


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


def test_render_synth_report_nine_canonical_lines(mini_bundle):
    text = render_synth_report(mini_bundle)
    assert text.endswith("\n")
    assert "%" not in text
    lines = text.splitlines()
    assert len(lines) == 9
    assert [line.split()[0] for line in lines] == list(_REPORT_NAMES)
    gold = summarize(as_cases(mini_bundle))
    counts = collect_counts(mini_bundle)
    assert counts["category_match"] == gold["category_match"]
    assert counts["outcome_match"] == gold["outcome_match"]
    assert counts["template_stable"] == gold["form_stable"]
    assert counts["relevant_in_top"] == gold["relevant_in_top"]
    for index, name in enumerate(
        ("category_match", "outcome_match", "template_stable", "relevant_in_top")
    ):
        hit, total = counts[name]
        assert lines[index] == f"{name} {hit} {total}"
    assert lines[4] == "resume_ok 0 0"
    assert render_synth_report(mini_bundle) == text


def test_collect_counts_keys_match_report(mini_bundle):
    counts = collect_counts(mini_bundle)
    assert list(counts) == list(_REPORT_NAMES)
    assert counts["resume_ok"] == (0, 0)
    assert counts["grade_separation"][1] == 216
    assert counts["sim_competent"][1] == 72
    assert counts["sim_key_leak"][1] == 72
    assert counts["sim_adjacent"][1] == 72
    text = render_synth_report(mini_bundle)
    for name, (hit, total) in counts.items():
        assert f"{name} {hit} {total}" in text


def test_collect_counts_resume_ok_ignores_golden_pdfs(mini_bundle, monkeypatch):
    monkeypatch.setattr("product.matching.golden_run._resume_counts", lambda: (99, 99))
    counts = collect_counts(mini_bundle)
    assert counts["resume_ok"] == (0, 0)
    assert "resume_ok 0 0" in render_synth_report(mini_bundle)


def test_annotate_relevant_stretch_absent_aligned_underclaim_present():
    profiles = [
        {
            "profile_id": 1,
            "specialization": "backend",
            "declared_grade": "middle",
            "true_grade": "middle",
            "claim_kind": "aligned",
        },
        {
            "profile_id": 2,
            "specialization": "backend",
            "declared_grade": "middle",
            "true_grade": "junior",
            "claim_kind": "stretch",
        },
        {
            "profile_id": 3,
            "specialization": "backend",
            "declared_grade": "middle",
            "true_grade": "senior",
            "claim_kind": "underclaim",
        },
        {
            "profile_id": 4,
            "specialization": "frontend",
            "declared_grade": "middle",
            "true_grade": "middle",
            "claim_kind": "aligned",
        },
        {
            "profile_id": 5,
            "specialization": "backend",
            "declared_grade": "middle",
            "true_grade": "middle",
            "claim_kind": "aligned",
        },
        {
            "profile_id": 6,
            "specialization": "backend",
            "declared_grade": "senior",
            "true_grade": "senior",
            "claim_kind": "aligned",
        },
    ]
    needs = [
        {
            "id": "backend-middle",
            "category_id": "backend:middle",
            "relevant_profile_ids": [],
        }
    ]
    bundle = {"profiles": profiles, "needs": needs}
    filled = annotate_relevant(bundle)
    assert filled is not bundle
    assert needs[0]["relevant_profile_ids"] == []
    assert filled["needs"][0]["relevant_profile_ids"] == [1, 3, 5]


def test_full_shards_report_denominators():
    bundle = load_shards()
    assert bundle["n"] == 1000
    assert bundle["shards"] == [1, 2, 3, 4]
    counts = collect_counts(bundle)
    gold = summarize(as_cases(bundle))
    assert counts["category_match"] == (1000, 1000)
    assert counts["outcome_match"] == gold["outcome_match"]
    assert counts["template_stable"] == gold["form_stable"]
    # Иной допустимый итог и балл шарда. «other» при живом test_score — CategoryError,
    # это проверка категории, не метрики. outcome_match читает кабинет, не эти поля.
    poisoned = dict(bundle)
    poisoned["profiles"] = [
        dict(row, expected_outcome="nope", test_outcome="suggest_lower", test_score=1)
        for row in bundle["profiles"]
    ]
    assert collect_counts(poisoned)["outcome_match"] == counts["outcome_match"]
    assert counts["relevant_in_top"] == (36, 36)
    assert counts["resume_ok"] == (0, 0)
    assert counts["grade_separation"] == (215, 216)
    assert counts["sim_competent"] == (72, 72)
    assert counts["sim_key_leak"] == (3, 72)
    assert counts["sim_adjacent"] == (0, 72)


def test_runner_main_incomplete_shards(monkeypatch):
    monkeypatch.setattr(
        synth_eval_run,
        "load_shards",
        lambda: {"shards": [], "n": 0, "profiles": [], "needs": [], "template_pairs": []},
    )
    with pytest.raises(SystemExit, match="shards_incomplete"):
        synth_eval_run.main()

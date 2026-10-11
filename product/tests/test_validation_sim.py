"""Проверки пакета product.validation_sim: данные, сборка варианта, ключ, симуляция.

ТЗ §2 «устойчивость к распространению заданий»; ТЗ §3.4 «процедура валидации»; ТЗ §6 критерий 2.
"""

from __future__ import annotations

import random

from product.validation_sim import bank as bank_mod
from product.validation_sim import candidates as cand_mod
from product.validation_sim import simulate as sim
from product.validation_sim.form import History, Exposure, assemble_form, public_view, slot_plan
from product.validation_sim.scale import GRADES, band


def _index() -> bank_mod.BankIndex:
    return bank_mod.BankIndex(bank_mod.build_bank(seed=1))


def test_bank_is_deterministic_and_complete():
    first, second = bank_mod.build_bank(seed=5), bank_mod.build_bank(seed=5)
    assert first == second
    index = bank_mod.BankIndex(first)
    for grade in GRADES:
        families = index.families("backend", grade)
        assert len(families) == bank_mod.FAMILIES_PER_PAIR
        assert all(len(f["variants"]) == bank_mod.VARIANTS_PER_FAMILY for f in families)


def test_candidates_csv_and_json_match(tmp_path):
    rows = cand_mod.generate(n=30, seed=3)
    csv_path, json_path = tmp_path / "c.csv", tmp_path / "c.json"
    cand_mod.save(rows, seed=3, csv_path=csv_path, json_path=json_path)
    assert cand_mod.load(csv_path) == cand_mod.load(json_path)
    for row in rows:
        lo, hi = band(row["true_grade"])
        assert lo <= row["theta"] <= hi


def test_shipped_data_files_load():
    assert len(cand_mod.load(cand_mod.JSON_PATH)) == 500
    assert len(cand_mod.load(cand_mod.CSV_PATH)) == 500
    bank_mod.load_bank()


def test_blueprint_counts():
    assert [(s, n) for s, _lvl, n in slot_plan("middle")] == [("below", 3), ("at", 6), ("above", 3)]
    assert [(s, n) for s, _lvl, n in slot_plan("junior")] == [("at", 9), ("above", 3)]
    assert [(s, n) for s, _lvl, n in slot_plan("lead")] == [("below", 3), ("at", 9)]


def test_public_view_hides_key_and_difficulty():
    form = assemble_form(_index(), "backend", "middle", random.Random(1), History(), Exposure())
    assert len(form["items"]) == 12
    for row in public_view(form):
        assert set(row) <= {"item_id", "type", "text", "options", "answer_space"}
        for option in row.get("options", []):
            assert option["id"] not in {"a", "b", "c"}  # одноразовые id, а не буквы


def test_retake_uses_other_families():
    index, rng, history, exposure = _index(), random.Random(2), History(), Exposure()
    first = assemble_form(index, "data", "senior", rng, history, exposure)
    second = assemble_form(index, "data", "senior", rng, history, exposure)
    at_first = {i["family_id"] for i in first["items"] if i["slot"] == "at"}
    at_second = {i["family_id"] for i in second["items"] if i["slot"] == "at"}
    assert not at_first & at_second  # 12 семейств уровня: 6 + 6 разных


def test_small_simulation_beats_tricks():
    index = _index()
    cands = cand_mod.generate(n=60, seed=4)
    tricks = sim.exp_tricks(lambda: sim.NewSystem(index, 9), cands)
    assert tricks["pattern_bca"]["pass_pct"] < 10
    assert tricks["random"]["pass_pct"] < 10
    acc = sim.exp_accuracy_retest(lambda: sim.NewSystem(index, 9), cands)
    assert acc["within1_pct"] >= 90


# --- Тест платформы: результат с уверенностью, доп. блоки, накопление, пилот -

from product.validation_sim import adaptive, irt  # noqa: E402
from product.validation_sim.form import assemble_targeted  # noqa: E402


def test_assign_is_cautious_and_reports_confidence():
    # Уровень чуть ниже границы middle/senior (0.6), погрешность 0.2:
    # P(senior) < 60% — осторожный порог оставляет middle со статусом «у границы».
    weights = irt.update(irt.prior_weights(0.55, 0.2), [])
    result = adaptive.assign(weights, threshold=0.85)
    assert result["grade"] == "middle"
    assert result["status"] == "у границы"
    assert 0 <= result["confidence"] <= 1


def test_targeted_block_matches_boundary_and_skips_seen():
    index, history = _index(), History()
    block = assemble_targeted(index, "backend", "middle", 0.6, 6, random.Random(3), history, Exposure())
    assert len(block["items"]) == 6
    assert all(abs(item["b"] - 0.6) < 0.8 for item in block["items"])
    again = assemble_targeted(index, "backend", "middle", 0.6, 6, random.Random(3), history, Exposure())
    assert not {i["family_id"] for i in block["items"]} & {i["family_id"] for i in again["items"]}


def test_shipped_pilot_file_loads():
    pilot = adaptive.load_pilot()
    assert 0.5 <= pilot["confident_threshold"] <= 0.99
    assert isinstance(pilot["retired"], list)


def test_platform_test_accumulates_and_beats_tricks():
    index = _index()
    cands = cand_mod.generate(n=40, seed=6)
    system = adaptive.AdaptiveSystem(index, 5)
    result = adaptive.run_chain_v2(system, cands[0])
    assert result["items"] >= 12 and result["status"] in {"уверенно", "у границы", "не подтверждён"}
    tricks = sim.exp_tricks(lambda: adaptive.AdaptiveSystem(index, 5), cands, strategies=("pattern_bca",))
    assert tricks["pattern_bca"]["pass_pct"] < 10


def test_shipped_bank_matches_catalog_texts():
    """bank.json хранит копии текстов stems.py: после правки каталога банк пересобирают."""
    from product.matching.stems import catalog

    texts = {}
    for qset in catalog():
        for q in qset["questions"]:
            ids = [c["id"] for c in q["choices"]]
            texts[q["text"]] = ([c["text"] for c in q["choices"]], ids.index(q["correct"]))
    for family in bank_mod.load_bank()["families"]:
        sample = family["sample"]
        assert texts[sample["text"]] == (sample["options"], sample["correct_index"]), family["family_id"]


def test_live_view_keeps_own_text_and_skips_repeats():
    """Свой текст сохраняет тип. Без текста — judge. Повтор образца в кабинет не входит."""
    bare = {
        "schema_version": 1,
        "types": bank_mod.TYPES,
        "families": [
            {
                "family_id": "plain",
                "type": "single",
                "sample": {"reused": False, "text": "образец"},
                "variants": [{"text": None}],
            },
            {
                "family_id": "typed",
                "type": "multi",
                "sample": {"reused": False},
                "variants": [{"text": "свой вопрос", "answer": 5}],
            },
            {
                "family_id": "repeat",
                "type": "order",
                "sample": {"reused": True, "text": "повтор"},
                "variants": [{"text": "текст повтора не возвращает семейство", "answer": 7}],
            },
            {
                "family_id": "no-sample",
                "type": "numeric",
                "sample": None,
                "variants": [{"text": "без образца", "answer": 9}],
            },
        ],
    }
    kinds = {row["family_id"]: row["type"] for row in bank_mod.live_view(bare)["families"]}
    assert kinds == {"plain": "judge", "typed": "multi"}

    bank = bank_mod.load_bank()
    live = bank_mod.live_view(bank)
    assert len(live["families"]) == 216
    assert not any(row["sample"].get("reused") for row in live["families"])
    assert not any(row["family_id"].rsplit(".", 1)[-1] in {"f10", "f11", "f12"} for row in live["families"])
    by_id = {row["family_id"]: row for row in bank["families"]}
    counts: dict[str, int] = {}
    for row in live["families"]:
        source = by_id[row["family_id"]]
        own = any(variant.get("text") for variant in source["variants"])
        if own:
            assert row["type"] == source["type"]
            assert row["type"] in {"multi", "order", "numeric"}
            assert all(
                variant.get("text") and variant.get("answer") not in (None, 0, 1, 2)
                for variant in row["variants"]
            )
        else:
            assert row["type"] == "judge"
            assert source["type"] == "single"
            assert not any(variant.get("text") for variant in row["variants"])
        counts[row["type"]] = counts.get(row["type"], 0) + 1
    assert counts == {"judge": 120, "multi": 48, "order": 24, "numeric": 24}
    assert any(row["sample"].get("reused") and row["type"] in {"order", "numeric"} for row in bank["families"])


def test_typed_item_key_is_variant_answer():
    """Ключ multi/order/numeric — answer варианта, не случайный номер."""
    bank = bank_mod.live_view(bank_mod.build_bank(seed=1))
    index = bank_mod.BankIndex(bank)
    by_id = {variant["variant_id"]: variant for family in bank["families"] for variant in family["variants"]}
    form = assemble_form(index, "backend", "middle", random.Random(4), History(), Exposure())
    typed = [item for item in form["items"] if item["type"] in {"multi", "order", "numeric"}]
    judges = [item for item in form["items"] if item["type"] == "judge"]
    assert typed and judges
    for item in typed:
        assert "options" not in item
        assert form["key"][item["item_id"]] == by_id[item["item_id"]]["answer"]
        assert form["key"][item["item_id"]] not in (0, 1, 2)
    for item in judges:
        assert form["key"][item["item_id"]] in {option["id"] for option in item["options"]}

"""Прогон SYNTH-EVAL. Печатает девять канонических строк записки.

ТЗ §2 и §3.4: итог и стабильность берёт кабинетный тест, как ``GET /validation``.
Сравнение ``expected_outcome`` с ``test_outcome`` шарда сюда не входит.
"""

from __future__ import annotations

import sys

from product.matching.golden_run import grade_separation_counts, summarize
from product.matching.simulate import simulation_counts
from product.matching.synth_eval import as_cases, load_shards

_REPORT_ORDER = (
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


def annotate_relevant(bundle: dict) -> dict:
    order = ("junior", "middle", "senior", "lead")
    out = dict(bundle)
    needs = []
    for need in bundle["needs"]:
        row = dict(need)
        spec, need_grade = str(row["category_id"]).split(":", 1)
        need_rank = order.index(need_grade)
        ids = []
        for profile in bundle["profiles"]:
            if profile["specialization"] != spec:
                continue
            if profile["declared_grade"] != need_grade:
                continue
            if order.index(profile["true_grade"]) < need_rank:
                continue
            ids.append(profile["profile_id"])
        row["relevant_profile_ids"] = ids
        needs.append(row)
    out["needs"] = needs
    return out


def _synth_case_counts(data: dict) -> dict[str, tuple[int, int]]:
    """Категория и топ — по шарду. Итог и стабильность — тест кабинета.

    ``summarize`` считает outcome_match и form_stable на вариантах банка,
    не на полях шарда. Подмена этих полей число не двигает.
    """
    gold = summarize(as_cases(data))
    return {
        "category_match": gold["category_match"],
        "outcome_match": gold["outcome_match"],
        "template_stable": gold["form_stable"],
        "relevant_in_top": gold["relevant_in_top"],
    }


def collect_counts(bundle: dict | None = None) -> dict[str, tuple[int, int]]:
    data = annotate_relevant(load_shards() if bundle is None else bundle)
    gold = _synth_case_counts(data)
    separated, total = grade_separation_counts()
    sim = simulation_counts()
    values = {**gold, "resume_ok": (0, 0), "grade_separation": (separated, total), **sim}
    return {name: values[name] for name in _REPORT_ORDER}


def render_synth_report(bundle: dict | None = None) -> str:
    counts = collect_counts(bundle)
    lines = [f"{name} {counts[name][0]} {counts[name][1]}" for name in _REPORT_ORDER]
    return "\n".join(lines) + "\n"


def main() -> None:
    bundle = load_shards()
    if len(bundle.get("shards") or []) != 4 or bundle.get("n") != 1000:
        raise SystemExit("shards_incomplete")
    sys.stdout.write(render_synth_report(bundle))


if __name__ == "__main__":
    main()

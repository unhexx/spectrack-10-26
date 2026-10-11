"""Прогон валидации: тест платформы и эталоны сравнения на одних и тех же кандидатах.

ТЗ §2 и §3.4: «собственная процедура валидации решения и её результаты»; ТЗ §6 критерий 2.

Запуск из корня репозитория (после ``generate``):

    python -m product.validation_sim.run
    python -m product.validation_sim.run --candidates product/validation_sim/data/candidates.csv --seed 11

Результаты в ``product/validation_sim/out``:
    results.json    все показатели (машиночитаемо)
    report.md       таблицы для сопроводительной записки (ТЗ §3.4)
    per_candidate.csv  итог каждого кандидата: истинный грейд / эталоны / платформа (статус, уверенность)
    validation_charts.png  графики, если установлен matplotlib (см. plot.py)
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from product.validation_sim import bank as bank_mod
from product.validation_sim import candidates as cand_mod
from product.validation_sim import adaptive
from product.validation_sim import simulate as sim
from product.validation_sim.baseline import BaselineSystem
from product.validation_sim.scale import GRADES

OUT_DIR = Path(__file__).resolve().parent / "out"


def _strip(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def run(candidates_path: Path, bank_path: Path, seed: int, out_dir: Path) -> dict:
    started = time.time()
    cands = cand_mod.load(candidates_path)
    index = bank_mod.BankIndex(bank_mod.load_bank(bank_path))

    # Фабрики: каждый эксперимент получает свежую систему с собственным seed.
    def new_system(offset: int = 0):
        return lambda: sim.NewSystem(index, seed + offset)

    def old_system(offset: int = 0):
        return lambda: BaselineSystem(seed + offset)

    baseline_sets = BaselineSystem.set_ids()

    results: dict = {"seed": seed, "candidates": len(cands), "systems": {}}
    per_candidate_finals = {}
    for key, factory, universe in (
        ("old", old_system, baseline_sets),
        ("new", new_system, index.all_variant_ids()),
    ):
        acc = sim.exp_accuracy_retest(factory(1), cands)
        block = {
            "accuracy": _strip(acc),
            "separation": sim.exp_separation(factory(2), cands),
            "tricks": sim.exp_tricks(factory(3), cands),
            "leakage": sim.exp_leakage(factory(4), cands, universe, seed),
            "items": sim.item_analysis(acc["_item_log"]),
        }
        if key == "new":
            block["exposure"] = sim.exposure_stats(acc["_system"], len(universe))
        else:
            block["key_known_pass_pct"] = sim.exp_key_known(factory(5), cands)
        results["systems"][key] = block
        # итог по кандидатам для per_candidate.csv (отдельный прогон той же фабрики)
        system = factory(6)()
        per_candidate_finals[key] = [sim.run_chain(system, c)[0] for c in cands]

    # Тест платформы: результат с уверенностью, доп. блоки, накопление, пилот,
    # осторожный порог. Две конфигурации банка:
    #   v2_live — банк кабинета live_view: свой текст сохраняет тип
    #             (multi / order / numeric, ответ — число), без текста — judge;
    #   v2      — полный банк, включая повторы образца, не отложенные форматы.
    v2_first = {}
    for key, view, pilot_path in (
        ("v2_live", bank_mod.live_view(index.bank), adaptive.PILOT_LIVE_PATH),
        ("v2", index.bank, adaptive.PILOT_PATH),
    ):
        v_index = bank_mod.BankIndex(view)
        pilot = adaptive.load_pilot(pilot_path)
        if not pilot_path.is_file():
            pilot = adaptive.run_pilot(v_index)
            adaptive.save_pilot(pilot, pilot_path)

        def v2_system(offset: int = 0, v_index=v_index, pilot=pilot):
            return lambda: adaptive.AdaptiveSystem(v_index, seed + offset, pilot["retired"],
                                                   confident=pilot["confident_threshold"])

        v2 = adaptive.exp_v2(v_index, cands, pilot)
        results["systems"][key] = {
            "main": _strip(v2),
            "tricks": sim.exp_tricks(v2_system(3), cands),
            "leakage": sim.exp_leakage(v2_system(4), cands, v_index.all_variant_ids(), seed),
            "pilot": {
                "families_analysed": pilot["families_analysed"],
                "retired": len(pilot["retired"]),
                "confident_threshold": pilot["confident_threshold"],
                "target_accuracy": pilot["target_accuracy"],
            },
        }
        v2_first[key] = v2["_first"]

    results["texts"] = sim.longest_is_correct_share(index)
    results["seconds"] = round(time.time() - started, 1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out_dir / "per_candidate.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["candidate_id", "specialization", "theta", "true_grade", "declared_grade",
                         "old_final_grade", "new_final_grade",
                         "live_grade", "live_status", "live_confidence", "live_position", "live_items",
                         "v2_grade", "v2_status", "v2_confidence", "v2_position", "v2_items"])
        for c, old, new, lv, v in zip(cands, per_candidate_finals["old"], per_candidate_finals["new"],
                                      v2_first["v2_live"], v2_first["v2"]):
            row = [c["candidate_id"], c["specialization"], c["theta"], c["true_grade"],
                   c["declared_grade"], old or "не подтверждён", new or "не подтверждён"]
            for res in (lv, v):
                row += [res["grade"] or "не подтверждён", res["status"], res["confidence"],
                        res["position"] or "", res["items"]]
            writer.writerow(row)
    (out_dir / "report.md").write_text(render_report(results), encoding="utf-8")
    return results


def render_report(r: dict) -> str:
    """Отчёт для сопроводительной записки: эталоны и тест платформы в двух конфигурациях."""
    old, new = r["systems"]["old"], r["systems"]["new"]
    live, full = r["systems"]["v2_live"], r["systems"]["v2"]

    def mm(block: dict, key: str) -> str:
        cell = block["main"]["summary"][key]
        return f"{cell['mean']} ({cell['min']}–{cell['max']})"

    head = "| Показатель | Единый набор | Банк без доборов | Платформа | Платформа + форматы |"
    sep = "| --- | --- | --- | --- | --- |"
    lines = [
        "# Валидация механики тестирования: симуляция",
        "",
        f"Кандидатов: {r['candidates']}, seed {r['seed']}, время прогона {r['seconds']} с.",
        "",
        "- **Единый набор** — эталон «один набор заданий для всех» (ТЗ §2): 3 вопроса, общий ключ b-c-a "
        "(`product/validation_sim/baseline.py`).",
        "- **Банк без доборов** — банк семейств, 12 вопросов по плану 3/6/3, оценка уровня (IRT), без доборов и уверенности.",
        "- **Платформа** — то, что работает в кабинете (`product/matching/assessment.py`, `bank.live_view`): "
        "часть вопросов — judge, три варианта «верно / неверно»; часть — одно число по своему тексту "
        "multi, order или numeric. Результат с уверенностью, доп. блоки у границы, накопление, пилот, "
        "осторожный порог.",
        "- **Платформа + форматы** — тот же тест на полном банке, а не форматы «когда добавят».",
        "",
        f"Для «Платформа» — среднее по {live['main']['repeats']} прогонам, в скобках мин–макс. Длины вариантов "
        "ответа в каталоге выровнены, чтобы длина не подсказывала ответ.",
        "",
        "## Сводка",
        "",
        head,
        sep,
        f"| Итоговый грейд = истинный, % | {old['accuracy']['exact_pct']} | {new['accuracy']['exact_pct']} | "
        f"{mm(live, 'exact_pct')} | {mm(full, 'exact_pct')} |",
        f"| **«Уверенно»: точность, %** | — | — | **{mm(live, 'confident_accuracy_pct')}** | "
        f"**{mm(full, 'confident_accuracy_pct')}** |",
        f"| «Уверенно»: доля кандидатов, % | — | — | {mm(live, 'confident_share_pct')} | {mm(full, 'confident_share_pct')} |",
        f"| Ошибка не больше одной ступени, % | {old['accuracy']['within1_pct']} | {new['accuracy']['within1_pct']} | "
        f"{mm(live, 'within1_pct')} | {mm(full, 'within1_pct')} |",
        f"| Грейд завышен, % | {old['accuracy']['overrated_pct']} | {new['accuracy']['overrated_pct']} | "
        f"{mm(live, 'overrated_pct')} | {mm(full, 'overrated_pct')} |",
        f"| Тот же грейд при пересдаче, % | {old['accuracy']['retest_same_pct']} | {new['accuracy']['retest_same_pct']} | "
        f"{mm(live, 'retest_same_pct')} | {mm(full, 'retest_same_pct')} |",
        f"| Вопросов за попытку | 3 | 12 | {mm(live, 'items_per_attempt')} | {mm(full, 'items_per_attempt')} |",
        "",
        "## Результат с уверенностью",
        "",
    ]
    for title, block in (("Платформа", live), ("Платформа + форматы", full)):
        pilot = block["pilot"]
        lines.append(
            f"- {title}: порог «уверенно» {pilot['confident_threshold']} — наименьший, при котором на ОТДЕЛЬНОЙ "
            f"пилотной группе (1000 кандидатов) точность среди «уверенно» не ниже {round(100 * pilot['target_accuracy'])}% "
            f"+ 0.5 п.п.; выведено {pilot['retired']} из {pilot['families_analysed']} тем. Статусы: "
            + ", ".join(f"{k} — {v}" for k, v in block["main"]["statuses"].items()) + "."
        )
    lines += [
        "",
        "| Порог уверенности | Платформа: доля / точность, % | Платформа + форматы: доля / точность, % |",
        "| --- | --- | --- |",
    ]
    live_curve = {round(p["threshold"], 2): p for p in live["main"]["curve"]}
    for point in full["main"]["curve"]:
        tau = round(point["threshold"], 2)
        if round(tau * 100) % 5:
            continue
        lp = live_curve.get(tau)
        left = f"{lp['coverage_pct']} / {lp['accuracy_pct']}" if lp else "—"
        lines.append(f"| {tau:.2f} | {left} | {point['coverage_pct']} / {point['accuracy_pct']} |")
    lines += [
        "",
        "## Уловки: доля «сдал» на заявленный грейд, %",
        "",
        "| Стратегия | Единый набор | Банк без доборов | Платформа | Платформа + форматы |",
        sep,
        f"| Должны сдать по истинному уровню (эталон) | {old['tricks']['ideal_pass_pct']} | "
        f"{new['tricks']['ideal_pass_pct']} | {live['tricks']['ideal_pass_pct']} | {full['tricks']['ideal_pass_pct']} |",
    ]
    names = {"honest": "Честно (для сравнения)", "random": "Наугад", "always_b": "Всегда «b»",
             "pattern_bca": "Выученный ключ b-c-a", "longest": "Самый длинный ответ"}
    for key, title in names.items():
        lines.append(f"| {title} | {old['tricks'][key]['pass_pct']} | {new['tricks'][key]['pass_pct']} | "
                     f"{live['tricks'][key]['pass_pct']} | {full['tricks'][key]['pass_pct']} |")
    lines += [
        "",
        "## Утечка: кандидат идёт на ступень выше истинной, доля «сдал», %",
        "",
        "| Доля утёкшего банка | Единый набор | Банк без доборов | Платформа | Платформа + форматы |",
        sep,
    ]
    for share in old["leakage"]["pass_pct_by_share"]:
        lines.append(f"| {float(share):.0%} | {old['leakage']['pass_pct_by_share'][share]} | "
                     f"{new['leakage']['pass_pct_by_share'][share]} | {live['leakage']['pass_pct_by_share'][share]} | "
                     f"{full['leakage']['pass_pct_by_share'][share]} |")
    lines += [
        f"| Известен ключ b-c-a | {old['key_known_pass_pct']} | — | — | — |",
        "",
        "## Точность по истинному грейду, %",
        "",
        "| Истинный грейд | Единый набор | Банк без доборов | Платформа | Платформа + форматы |",
        sep,
    ]
    for g in GRADES:
        lines.append(f"| {g} | {old['accuracy']['exact_by_true_grade'][g]} | {new['accuracy']['exact_by_true_grade'][g]} | "
                     f"{live['main']['exact_by_true_grade'][g]} | {full['main']['exact_by_true_grade'][g]} |")
    lines += [
        "",
        "## Разделяющая сила: средний % верных на тесте уровня middle по истинному грейду",
        "",
        "| Истинный грейд | Единый набор | Банк без доборов |",
        "| --- | --- | --- |",
    ]
    for g in GRADES:
        o, n = old["separation"]["by_true_grade"][g], new["separation"]["by_true_grade"][g]
        lines.append(f"| {g} | {o['mean']} ± {o['sd']} | {n['mean']} ± {n['sd']} |")
    lines.append("")
    for pair in old["separation"]["cohen_d"]:
        lines.append(f"- Cohen's d {pair}: единый набор {old['separation']['cohen_d'][pair]}, банк без доборов {new['separation']['cohen_d'][pair]}")
    exp = new["exposure"]
    lines += [
        "",
        "## Качество вопросов и расход банка",
        "",
        f"- Корреляция «вопрос — остальной тест»: банк без доборов {new['items']['mean_rpb']} по {new['items']['items_analysed']} "
        f"темам, единый набор {old['items']['mean_rpb']} по {old['items']['items_analysed']} вопросам.",
        f"- Использовано вариантов банка (банк без доборов): {exp['variants_used_pct']}%, показов на вариант "
        f"в среднем {exp['mean_shows']}, максимум {exp['max_shows']}.",
        f"- Верный ответ — самый длинный: {r['texts']['old_pct']}% вопросов каталога (нейтрально ~33%).",
        "",
        "## Допущения",
        "",
        "- Ответы моделируются IRT (3 параметра) с ошибкой калибровки сложности (sd 0.15).",
        "- Старым вопросам заданы те же параметры, что новым того же уровня — сравниваются механики.",
        "- Интервалы пересдачи по времени не моделируются.",
        "- Точность «уверенно» опирается на 100–250 решений и колеблется между прогонами на ±2 п.п. — "
        "поэтому среднее и мин–макс по 5 прогонам.",
        "- Пилотная группа (seed 7, 1000 кандидатов) не пересекается с 500 кандидатами отчёта.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Симуляционная валидация теста")
    parser.add_argument("--candidates", type=Path, default=cand_mod.JSON_PATH)
    parser.add_argument("--bank", type=Path, default=bank_mod.DEFAULT_PATH)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args(argv)
    results = run(args.candidates, args.bank, args.seed, args.out)
    print((args.out / "report.md").read_text(encoding="utf-8"))
    if not args.no_plot:
        try:
            from product.validation_sim.plot import plot
        except ImportError:
            print("matplotlib не установлен: графики пропущены (pip install matplotlib)")
            return
        print("charts:", plot(results, args.out))


if __name__ == "__main__":
    main()

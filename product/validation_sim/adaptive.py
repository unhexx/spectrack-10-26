"""Тест платформы: результат с уверенностью, доп. блоки у границы, накопление
между попытками, осторожный порог, вывод слабых семейств по пилоту.

ТЗ §2 «устойчивость к распространению заданий… сопоставимая сложность»; ТЗ §2
«по результату теста грейд не понижается принудительно»; ТЗ §6 критерий 2.

Почему именно так (см. отчёт симуляции): 12 вопросов дают погрешность уровня
около ±0.4 при ширине грейда 1.2. Кандидат у самой границы грейдов неизбежно
иногда попадает в соседний грейд — у любого теста такой длины. Поэтому
главное улучшение — не «угадать» грейд пограничного кандидата любой ценой,
а честно сказать, насколько тест уверен (Q&A: неподтверждённое показывать
со статусом, а не скрывать), и добрать информацию там, где она нужна.

Пять механизмов поверх базовой оценки уровня (``simulate.NewSystem``):

1. **Результат с уверенностью.** Кроме грейда тест сообщает вероятность, что
   грейд верный, и положение внутри грейда (нижняя / средняя / верхняя треть).
   Уверенность ≥ CONFIDENT — статус «подтверждён уверенно», ниже — «у границы».
2. **Доп. блок у границы.** Если после 12 вопросов вероятность по спорной
   границе между 20% и 80%, выдаются ещё EXTRA_ITEMS вопросов со сложностью
   именно этой границы. Уверенные результаты заканчивают тест на 12 вопросах.
3. **Накопление.** Каждая следующая попытка (тест ниже/выше) начинает не с
   нуля, а с распределения уровня, полученного раньше (байесовское обновление).
   Грейд ставят только тесты (Q&A: достижения ФСП на грейд не влияют).
4. **Пилот.** Семейства, которые на отдельной пилотной группе плохо различали
   кандидатов (корреляция с остальным тестом < 0.15), выводятся из сборки
   (``data/pilot.json``). Пилотная группа не пересекается с 500
   кандидатами, на которых считаются показатели.
5. **Осторожный порог.** Грейд g присваивается, только если P(уровень ≥ границы g)
   ≥ CAUTION. Завысить грейд хуже, чем занизить: работодатель зовёт senior,
   а приходит middle.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from product.validation_sim import irt
from product.validation_sim.bank import BankIndex
from product.validation_sim.form import History, assemble_form, assemble_targeted
from product.validation_sim.scale import GRADES, LOWER, band, next_grade, prev_grade
from product.validation_sim.scoring import check_answers
from product.validation_sim.simulate import NewSystem

CAUTION = 0.6        # п.5: нужна ≥60% уверенность, что уровень не ниже границы грейда
CONFIDENT = 0.85     # п.1: порог статуса «уверенно»; уточняется пилотом (см. run_pilot)
TARGET_ACCURACY = 0.93  # п.1: требуемая точность среди «уверенно» (задано командой)
PILOT_MARGIN = 0.005    # запас на разброс: на пилоте требуем target + 0.5 п.п.
EXTRA_ITEMS = 6      # п.2: размер доп. блока
MAX_EXTRA_BLOCKS = 3  # п.2: не больше трёх доп. блоков за попытку (≤ 30 вопросов всего)
BORDER_ZONE = (0.15, 0.85)  # п.2: вероятность по границе в этом интервале = «спорно»
MAX_ATTEMPTS = 3     # заявленный грейд + не больше двух шагов вниз/вверх

PILOT_PATH = Path(__file__).resolve().parent / "data" / "pilot.json"            # банк со всеми форматами (прогноз)
PILOT_LIVE_PATH = Path(__file__).resolve().parent / "data" / "pilot_live.json"  # банк кабинета: judge и свои multi/order/numeric


def load_pilot(path: Path = PILOT_PATH) -> dict:
    """Итоги пилота: выведенные семейства и порог «уверенно». Нет файла — значения по умолчанию."""
    if not Path(path).is_file():
        return {"retired": [], "confident_threshold": CONFIDENT}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assign(weights: list[float], threshold: float | None = None) -> dict:
    """Итог по распределению уровня: грейд, уверенность, положение в грейде.

    Грейд — самый высокий g, для которого P(θ ≥ граница g) ≥ CAUTION.
    Уверенность — вероятность, что θ лежит именно в интервале этого грейда.
    """
    threshold = CONFIDENT if threshold is None else threshold
    grade = None
    for g in GRADES:
        if irt.prob_at_least(weights, LOWER[g]) >= CAUTION:
            grade = g
    theta, se = irt.summary(weights)
    if grade is None:
        confidence = 1.0 - irt.prob_at_least(weights, LOWER["junior"])
        return {"grade": None, "confidence": round(confidence, 3), "theta": round(theta, 3),
                "se": round(se, 3), "position": None, "status": "не подтверждён"}
    lo, hi = band(grade)
    upper = LOWER[next_grade(grade)] if next_grade(grade) else float("inf")
    confidence = irt.prob_at_least(weights, lo) - (irt.prob_at_least(weights, upper) if upper != float("inf") else 0.0)
    share = min(max((theta - lo) / (hi - lo), 0.0), 0.999)
    position = ("нижняя", "средняя", "верхняя")[int(share * 3)]
    confidence = round(confidence, 3)  # одно и то же число в статусе, отчёте и кривой
    status = "уверенно" if confidence >= threshold else "у границы"
    return {"grade": grade, "confidence": round(confidence, 3), "theta": round(theta, 3),
            "se": round(se, 3), "position": position, "status": status}


def _responses(answered: list[tuple]) -> list[tuple[float, float, float, bool]]:
    return [(item["a"], item["b"], item["c"], ok) for item, ok in answered]


class AdaptiveSystem(NewSystem):
    """Тест платформы. Интерфейс ``take`` тот же, что у эталонов сравнения."""

    name = "Тест платформы (уверенность, доп. блоки, накопление, пилот)"

    def __init__(self, index: BankIndex, seed: int, retired: frozenset[str] = frozenset(),
                 accumulate: bool = True, confident: float | None = None) -> None:
        super().__init__(index, seed)
        self.retired = frozenset(retired)
        self.confident = CONFIDENT if confident is None else confident
        self.accumulate = accumulate
        self.weights: dict[str, list[float]] = {}

    def reset(self, candidate_id: str) -> None:
        """Забыть накопленное (для независимой пересдачи); история вопросов остаётся."""
        self.weights.pop(candidate_id, None)

    def _ask(self, form: dict, answer) -> list[tuple]:
        answers = {item["item_id"]: answer(item, form["key"][item["item_id"]], self.rng) for item in form["items"]}
        flags = check_answers(form, answers)
        return [(item, ok) for item, ok in zip(form["items"], flags)]

    def take(self, candidate: dict, grade: str, strategy: str = "honest",
             theta: float | None = None, leaked: set[str] | None = None) -> dict:
        cid, spec = candidate["candidate_id"], candidate["specialization"]
        history = self.history.setdefault(cid, History())
        answer = self._responder(strategy, candidate["theta"] if theta is None else theta, leaked)
        prior = self.weights.get(cid) if self.accumulate else None
        weights = prior or irt.prior_weights()

        form = assemble_form(self.index, spec, grade, self.rng, history, self.exposure, self.retired)
        answered = self._ask(form, answer)
        weights = irt.update(weights, _responses(answered))

        # п.2: доп. блоки, пока решение по одной из границ теста спорное
        # (не больше MAX_EXTRA_BLOCKS блоков, чтобы тест не растягивался).
        bounds = [LOWER[g] for g in (grade, next_grade(grade)) if g]
        extra = 0
        for _ in range(MAX_EXTRA_BLOCKS):
            probs = {b: irt.prob_at_least(weights, b) for b in bounds}
            disputed = [b for b, p in probs.items() if BORDER_ZONE[0] < p < BORDER_ZONE[1]]
            if not disputed:
                break
            target = min(disputed, key=lambda b: abs(probs[b] - 0.5))
            block = assemble_targeted(self.index, spec, grade, target, EXTRA_ITEMS, self.rng,
                                      history, self.exposure, self.retired)
            more = self._ask(block, answer)
            if not more:
                break  # банк для этой границы исчерпан
            weights = irt.update(weights, _responses(more))
            answered += more
            extra += len(more)
        self.weights[cid] = weights

        # Решение по попытке (п.5 — осторожный порог)
        upper = next_grade(grade)
        if upper and irt.prob_at_least(weights, LOWER[upper]) >= CAUTION:
            outcome, proposed = "suggest_higher", upper
        elif irt.prob_at_least(weights, LOWER[grade]) >= CAUTION:
            outcome, proposed = "confirm", grade
        else:
            outcome, proposed = "suggest_lower", prev_grade(grade)
        flags = [ok for _i, ok in answered]
        result = assign(weights, self.confident)
        result.update({
            "outcome": outcome,
            "proposed_grade": proposed,
            "percent": round(100 * sum(flags[:len(form["items"])]) / len(form["items"])),
            "n_items": len(flags),
            "extra_items": extra,
            "items": [(i["family_id"], ok) for i, ok in answered],
        })
        return result


def run_chain_v2(system: AdaptiveSystem, candidate: dict, strategy: str = "honest") -> dict:
    """Путь кандидата по ТЗ (тест на заявленный грейд → выше/ниже) с накоплением.
    Итог берётся из накопленного распределения: грейд, уверенность, статус."""
    grade = candidate["declared_grade"]
    items = attempts = 0
    seen: set[str] = set()
    last = None
    while grade and grade not in seen and attempts < MAX_ATTEMPTS:
        seen.add(grade)
        last = system.take(candidate, grade, strategy)
        attempts += 1
        items += last["n_items"]
        if last["outcome"] == "confirm":
            break
        grade = last["proposed_grade"]
    final = assign(system.weights[candidate["candidate_id"]], system.confident) if system.accumulate else last
    return {**final, "attempts": attempts, "items": items}


def run_pilot(index: BankIndex, n: int = 1000, seed: int = 7, weak_below: float = 0.15,
              target: float = TARGET_ACCURACY) -> dict:
    """Пилот на ОТДЕЛЬНОЙ группе (другой seed, id pilot-NNNN). Два шага:

    п.4 — группа проходит базовую оценку уровня; семейства с корреляцией «вопрос — остальной
    тест» ниже ``weak_below`` выводятся из сборки;
    п.1 — группа проходит тест платформы без этих семейств; выбирается наименьший
    порог уверенности, при котором точность среди «уверенно» ≥ ``target`` +
    PILOT_MARGIN (запас на то, что на другой группе точность чуть ниже).

    Показатели потом считаются на других 500 кандидатах — порог не подогнан
    под тот набор, на котором его проверяют. В продукте пилот — первые
    реальные попытки с экспертной разметкой.
    """
    from product.validation_sim import candidates as cand_mod
    from product.validation_sim import simulate as sim

    cohort = cand_mod.generate(n=n, seed=seed)
    for row in cohort:
        row["candidate_id"] = row["candidate_id"].replace("sim-", "pilot-")
    acc = sim.exp_accuracy_retest(lambda: sim.NewSystem(index, seed), cohort)
    analysis = sim.item_analysis(acc["_item_log"])
    retired = frozenset(analysis["_weak"])

    system = AdaptiveSystem(index, seed + 1, retired)
    results = [run_chain_v2(system, cand) for cand in cohort]
    curve = []
    for step in range(70, 99):
        tau = step / 100
        hits = [r["grade"] == c["true_grade"] for r, c in zip(results, cohort)
                if r["grade"] and r["confidence"] >= tau]
        if len(hits) < 0.05 * n:  # меньше 5% группы — точность по ним шумная
            break
        curve.append({"threshold": tau, "coverage_pct": round(100 * len(hits) / n, 1),
                      "accuracy_pct": round(100 * sum(hits) / len(hits), 1)})
    # Порог = наименьший τ, при котором точность среди «уверенно» на пилоте
    # не ниже target + PILOT_MARGIN. Пилот — 1000 кандидатов: на 500 кривая
    # слишком шумная и порог «прыгает» между прогонами.
    chosen = next((p["threshold"] for p in curve if p["accuracy_pct"] >= 100 * (target + PILOT_MARGIN)), None)
    return {
        "pilot_seed": seed,
        "pilot_size": n,
        "weak_below": weak_below,
        "families_analysed": analysis["items_analysed"],
        "retired": sorted(retired),
        "target_accuracy": target,
        "confident_threshold": chosen if chosen is not None else 0.95,
        "pilot_curve": curve,
    }


def save_pilot(pilot: dict, path: Path = PILOT_PATH) -> Path:
    payload = {"note": "Итоги пилота на отдельной синтетической группе: выведенные семейства и порог «уверенно».",
               **pilot}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def exp_v2(index: BankIndex, candidates: list[dict], pilot: dict, seeds=(11, 12, 13, 14, 15)) -> dict:
    """Показатели теста платформы на 500 кандидатах, повторённые на нескольких seed.

    Точность среди «уверенно» считается по ~250 решениям, поэтому разброс от
    прогона к прогону — около ±1.5 п.п. Отчёт даёт среднее и мин–макс, а не
    один удачный прогон.
    """
    runs = []
    first = None
    for seed in seeds:
        system = AdaptiveSystem(index, seed, pilot["retired"], confident=pilot["confident_threshold"])
        results = [run_chain_v2(system, c) for c in candidates]
        for c in candidates:
            system.reset(c["candidate_id"])
        retest = [run_chain_v2(system, c) for c in candidates]
        n = len(candidates)
        pairs = list(zip(results, candidates))
        confident = [(r, c) for r, c in pairs if r["status"] == "уверенно"]
        runs.append({
            "exact_pct": 100 * sum(r["grade"] == c["true_grade"] for r, c in pairs) / n,
            "within1_pct": 100 * sum(abs(_gi(r["grade"]) - _gi(c["true_grade"])) <= 1 for r, c in pairs) / n,
            "overrated_pct": 100 * sum(_gi(r["grade"]) > _gi(c["true_grade"]) for r, c in pairs) / n,
            "retest_same_pct": 100 * sum(a["grade"] == b["grade"] for a, b in zip(results, retest)) / n,
            "confident_share_pct": 100 * len(confident) / n,
            "confident_accuracy_pct": 100 * sum(r["grade"] == c["true_grade"] for r, c in confident) / max(1, len(confident)),
            "items_per_attempt": statistics.mean(r["items"] / r["attempts"] for r in results),
            "mean_attempts": statistics.mean(r["attempts"] for r in results),
        })
        if first is None:
            first = results
    summary = {}
    for key in runs[0]:
        values = [run[key] for run in runs]
        summary[key] = {"mean": round(statistics.mean(values), 1),
                        "min": round(min(values), 1), "max": round(max(values), 1)}
    # Кривая «доля уверенных решений — их точность» по первому прогону
    curve = []
    for step in range(50, 99):
        tau = step / 100
        # «не подтверждён» в кривую не входит — как и в статус «уверенно»
        hits = [r["grade"] == c["true_grade"] for r, c in zip(first, candidates)
                if r["grade"] and r["confidence"] >= tau]
        if hits:
            curve.append({"threshold": tau, "coverage_pct": round(100 * len(hits) / len(candidates), 1),
                          "accuracy_pct": round(100 * sum(hits) / len(hits), 1)})
    confusion = {g: {h: 0 for h in (*GRADES, "none")} for g in GRADES}
    by_grade = {g: [] for g in GRADES}
    statuses: dict[str, int] = {}
    for r, c in zip(first, candidates):
        confusion[c["true_grade"]][r["grade"] or "none"] += 1
        by_grade[c["true_grade"]].append(r["grade"] == c["true_grade"])
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {
        "repeats": len(seeds),
        "summary": summary,
        "curve": curve,
        "confusion": confusion,
        "exact_by_true_grade": {g: round(100 * sum(v) / max(1, len(v)), 1) for g, v in by_grade.items()},
        "statuses": statuses,
        "_first": first,
    }


def _gi(grade: str | None) -> int:
    return -1 if grade is None else GRADES.index(grade)

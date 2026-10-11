"""CPU-персоны для прогона жюри: три правила поведения кандидата на тесте грейда.

ТЗ §2 «Команда предлагает собственную процедуру оценки своего решения» и
ТЗ §2 «устойчивость к распространению заданий»; ТЗ §6, критерий 2.

Персоны проходят тот же тест, что и кабинет (``product.matching.assessment``):
на каждую пару «специализация × грейд» — три варианта с фиксированным seed,
всего 72 попытки. Без GPU, без сети и без инференса.

- ``sim_competent`` — знает всё до заявленного уровня включительно; «сдал» =
  тест подтвердил грейд или предложил выше. Ожидается 72 из 72.
- ``sim_key_leak`` — выучил чужой ключ по позициям: в каждом вопросе отмечает
  «верно» вариант на той же позиции, где он был верным у другого кандидата
  (повтор шаблона b, c, a). Перемешивание вариантов делает такой ключ бесполезным.
- ``sim_adjacent`` — истинный уровень на ступень ниже заявленного: знает только
  вопросы уровней ниже. Тест не должен его подтверждать.

Порога нет: строки показывают, как ведёт себя тест на этом запуске.
"""

from __future__ import annotations

import random
from functools import lru_cache

from product.matching.rules import load_rules

_VARIANTS_PER_PAIR = 3
_HIT = frozenset({"confirm", "suggest_higher"})
_LEAKED_POSITIONS = (1, 2, 0)  # «b, c, a» — позиции, а не одноразовые id вариантов


@lru_cache(maxsize=1)
def _forms() -> tuple[tuple[str, dict], ...]:
    """(заявленный грейд, вариант в формате хранения) — 24 пары × 3 варианта."""
    from product.matching import assessment
    from product.validation_sim.form import Exposure, History, assemble_form

    rules = load_rules()
    forms = []
    for n, spec in enumerate(rules["specializations"]):
        for k, grade in enumerate(rules["grades"]):
            for v in range(_VARIANTS_PER_PAIR):
                rng = random.Random(20261010 + 1000 * n + 10 * k + v)
                form = assemble_form(assessment.bank_index(), spec, grade, rng, History(), Exposure(),
                                     frozenset(assessment.pilot()["retired"]))
                forms.append((str(grade), {"items": assessment._stored(form, 0), "answers": {}}))
    return tuple(forms)


def _knows_up_to(stored: dict, level: int) -> dict:
    from product.matching import assessment

    grades = [str(g) for g in load_rules()["grades"]]
    answers = {}
    for item in stored["items"]:
        ok = grades.index(item["level"]) <= level
        answers[item["qid"]] = assessment.scored_answer(item, ok)
    return {**stored, "answers": answers}


def _leaked_key(stored: dict) -> dict:
    answers = {}
    for index, item in enumerate(stored["items"]):
        position = _LEAKED_POSITIONS[index % len(_LEAKED_POSITIONS)]
        options = item.get("options") or []
        if options:
            option = options[position % len(options)]
            answers[item["qid"]] = [option["id"]]
        else:
            # Позиция шаблона b-c-a. У multi/order/numeric ключ на эти номера не стоит.
            answers[item["qid"]] = position
    return {**stored, "answers": answers}


def _passed(answered: dict, grade: str) -> bool:
    from product.matching import assessment

    weights = assessment.update_weights(None, answered)
    outcome, _proposed = assessment.decide(weights, grade)
    return outcome in _HIT


def simulation_counts() -> dict[str, tuple[int, int]]:
    """Три пары (сдали, всего) для отчёта ``GET /validation``."""
    grades = [str(g) for g in load_rules()["grades"]]
    competent = leak = adjacent = 0
    forms = _forms()
    for grade, stored in forms:
        level = grades.index(grade)
        competent += _passed(_knows_up_to(stored, level), grade)
        leak += _passed(_leaked_key(stored), grade)
        adjacent += _passed(_knows_up_to(stored, level - 1), grade)
    n = len(forms)
    return {
        "sim_competent": (competent, n),
        "sim_key_leak": (leak, n),
        "sim_adjacent": (adjacent, n),
    }

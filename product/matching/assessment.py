"""Испытание грейда в кабинете: механика теста поверх банка семейств вопросов.

ТЗ §2: «Единый набор заданий для всех кандидатов не подходит — задания и ответы
быстро разойдутся… генерация уникальных заданий нейросетью не гарантирует
сопоставимую сложность. Придумайте подход, устойчивый к этим проблемам».
ТЗ §2.1 «Прохождение тестирования… результат автоматически определяет категорию».
Критерий оценки 2 (25%): устойчивость к распространению заданий, стабильность
грейда при повторе, дискриминативность, соответствие эталонной разметке.

Модуль — тонкий слой над ``product.validation_sim``: в кабинете работает ТОТ ЖЕ
код сборки варианта и оценки, который проверен симуляцией на 500 синтетических
кандидатах (``python -m product.validation_sim.run``). Здесь только перевод
в формат хранения попытки и в публичный вид для кандидата.

Как устроено (подробно — ``product/validation_sim/README.md``):

1. Вариант из 12 вопросов по единому плану 3/6/3 (уровень ниже / свой / выше),
   темы и сложность одинаковы для всех, конкретные вопросы — свои у каждого.
2. Формат judge — «верно / неверно» по трём вариантам, угадать 1 из 8.
   multi, order и numeric со своим текстом остаются своим типом: ответ — число,
   ключ попытки совпадает с answer варианта, угадывание — по-прежнему space.
3. Порядок вариантов перемешан, id вариантов одноразовые, ключ хранится только
   в записи попытки на сервере и в ответ API не попадает.
4. Уровень θ оценивается по IRT с учётом сложности каждого вопроса; у границы
   грейдов добавляются блоки по 6 вопросов (до трёх).
5. Предыдущие попытки этой специализации учитываются (накопление).
6. Итог: грейд, уверенность, статус «уверенно / у границы», положение в грейде.
   Порог «уверенно» и выведенные слабые темы — из пилота (``pilot_live.json``).
"""

from __future__ import annotations

import random
import secrets
from functools import lru_cache

from product.validation_sim import adaptive, irt
from product.validation_sim.bank import BankIndex, live_view, load_bank
from product.validation_sim.form import Exposure, History, assemble_form, assemble_targeted
from product.validation_sim.scale import LOWER, next_grade, prev_grade
from product.validation_sim.scoring import is_correct

ENGINE = "bank"  # идентификатор механики: поле template_id попытки и API

# Порядок полей вопроса в записи попытки (form_json). Ключ — только здесь.
_ITEM_FIELDS = ("qid", "item_id", "family_id", "slot", "level", "type", "a", "b", "c", "text", "options", "key")


@lru_cache(maxsize=1)
def bank_index() -> BankIndex:
    """Банк кабинета: без повторов образца, свой текст сохраняет тип вопроса."""
    return BankIndex(live_view(load_bank()))


@lru_cache(maxsize=1)
def pilot() -> dict:
    """Итоги пилота для банка кабинета: выведенные темы и порог «уверенно»."""
    return adaptive.load_pilot(adaptive.PILOT_LIVE_PATH)


def _rng() -> random.Random:
    # Случайность сборки варианта непредсказуема для кандидата.
    return random.Random(secrets.randbits(64))


def _stored(form: dict, start: int) -> list[dict]:
    """Вопросы сборки → записи попытки. qid сквозной: q1, q2, … по всей попытке."""
    rows = []
    for offset, item in enumerate(form["items"]):
        row = {name: item.get(name) for name in _ITEM_FIELDS if name not in ("qid", "key")}
        row["qid"] = f"q{start + offset + 1}"
        row["key"] = form["key"][item["item_id"]]
        rows.append(row)
    return rows


def new_form(spec: str, grade: str, seen: History, exposure: Exposure) -> dict:
    """Новый вариант попытки (12 вопросов) в формате хранения."""
    form = assemble_form(bank_index(), spec, grade, _rng(), seen, exposure, frozenset(pilot()["retired"]))
    return {"engine": ENGINE, "items": _stored(form, 0), "answers": {}, "blocks": 0}


def add_block(stored: dict, spec: str, grade: str, boundary: float, seen: History, exposure: Exposure) -> list[dict]:
    """Доп. блок у спорной границы. Возвращает новые записи вопросов (или [])."""
    block = assemble_targeted(bank_index(), spec, grade, boundary, adaptive.EXTRA_ITEMS, _rng(), seen,
                              exposure, frozenset(pilot()["retired"]))
    rows = _stored(block, len(stored["items"]))
    stored["items"].extend(rows)
    stored["blocks"] = int(stored.get("blocks", 0)) + (1 if rows else 0)
    return rows


def public_questions(rows: list[dict]) -> list[dict]:
    """Что видит кандидат: текст и варианты с одноразовыми id. Без ключа и сложности."""
    return [
        {
            "id": row["qid"],
            "type": row["type"],
            "text": row["text"],
            "choices": [{"id": opt["id"], "text": opt["text"]} for opt in row.get("options") or []],
        }
        for row in rows
    ]


def pending(stored: dict) -> list[dict]:
    """Вопросы попытки, на которые ещё нет ответа."""
    answers = stored.get("answers") or {}
    return [row for row in stored["items"] if row["qid"] not in answers]


def _canonical_int(raw: object) -> int | None:
    """Целое или его десятичная запись. Булево — не число."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("+"):
            text = text[1:]
        if text.lstrip("-").isdigit() and text not in ("", "-"):
            return int(text)
    return None


def normalize_answer(row: dict, raw: object) -> list[str] | int | None:
    """Ответ в том виде, в каком его сравнивает ``is_correct``.

    judge — список id вариантов, отмеченных «верно». Чужой id → None.
    multi / order / numeric — целое или его десятичная запись (в том числе
    список из одного такого значения, как обёртка API).
    """
    if row.get("type") in ("multi", "order", "numeric"):
        if isinstance(raw, list):
            if len(raw) != 1:
                return None
            raw = raw[0]
        return _canonical_int(raw)
    ids = {opt["id"] for opt in row.get("options") or []}
    if isinstance(raw, str):
        raw = [raw] if raw else []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        return None
    if any(x not in ids for x in raw):
        return None
    return sorted(set(raw))


def scored_answer(row: dict, correct: bool) -> list[str] | int:
    """Ответ, который ``is_correct`` сочтёт верным или заведомо неверным.

    Нужен симуляции кабинета: она пишет ответ сама, минуя ``normalize_answer``.
    """
    if row.get("type") in ("multi", "order", "numeric"):
        if correct:
            return row["key"]
        return 0 if row["key"] != 0 else 4
    return [row["key"]] if correct else []


def correct_flags(stored: dict) -> list[tuple[dict, bool]]:
    """(вопрос, верно ли) для всех отвеченных вопросов попытки."""
    answers = stored.get("answers") or {}
    return [(row, is_correct(row, answers[row["qid"]], row["key"])) for row in stored["items"]
            if row["qid"] in answers]


def update_weights(weights: list[float] | None, stored: dict) -> list[float]:
    """Распределение уровня с учётом ответов попытки (п.3 — накопление)."""
    base = weights if weights is not None else irt.prior_weights()
    return irt.update(base, [(row["a"], row["b"], row["c"], ok) for row, ok in correct_flags(stored)])


def disputed_boundary(weights: list[float], grade: str) -> float | None:
    """Спорная граница теста (вероятность в BORDER_ZONE) или None."""
    bounds = [LOWER[g] for g in (grade, next_grade(grade)) if g]
    probs = {b: irt.prob_at_least(weights, b) for b in bounds}
    lo, hi = adaptive.BORDER_ZONE
    disputed = [b for b, p in probs.items() if lo < p < hi]
    if not disputed:
        return None
    return min(disputed, key=lambda b: abs(probs[b] - 0.5))


def decide(weights: list[float], grade: str) -> tuple[str, str | None]:
    """Итог попытки по осторожному порогу (ТЗ §2: грейд не понижается принудительно)."""
    upper = next_grade(grade)
    if upper and irt.prob_at_least(weights, LOWER[upper]) >= adaptive.CAUTION:
        return "suggest_higher", upper
    if irt.prob_at_least(weights, LOWER[grade]) >= adaptive.CAUTION:
        return "confirm", grade
    return "suggest_lower", prev_grade(grade)


def summary(weights: list[float]) -> dict:
    """Грейд по всем ответам, уверенность, статус и положение в грейде."""
    return adaptive.assign(weights, pilot()["confident_threshold"])


def history_from(rows: list[dict]) -> History:
    """Что кандидат уже видел — из сохранённых попыток (пересдача без повторов)."""
    seen = History()
    for stored in rows:
        for item in stored.get("items") or []:
            seen.families.add(item["family_id"])
            seen.variants.add(item["item_id"])
    return seen


def exposure_from(rows: list[dict]) -> Exposure:
    """Сколько раз показан каждый вариант на платформе (контроль частоты показа)."""
    exposure = Exposure()
    for stored in rows:
        for item in stored.get("items") or []:
            exposure.add(item["family_id"], item["item_id"])
    return exposure


def blueprint(grade: str) -> list[dict]:
    """План теста на грейд для страницы «как вакансия превращается в тест» (ТЗ §2)."""
    from product.validation_sim.form import slot_plan

    names = {"below": "уровень ниже", "at": "заявленный уровень", "above": "уровень выше"}
    return [{"slot": names[slot], "level": level, "count": count} for slot, level, count in slot_plan(grade)]

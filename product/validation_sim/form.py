"""Сборка индивидуального варианта теста по единому плану (blueprint).

Главная идея защиты от утечки (ТЗ §2, Q&A «одинаковые задачи не должны
расходиться между кандидатами»):

1. **Единый план.** Тест на грейд g всегда состоит из 12 позиций:
   3 вопроса уровня ниже g, 6 — уровня g, 3 — уровня выше g.
   У junior нет уровня ниже, у lead — выше: эти позиции добираются уровнем g.
   Темы и сложность одинаковы для всех — результат сопоставим.
2. **Случайный вариант в каждой позиции.** Семейство и вариант внутри него
   выбираются случайно, порядок вопросов и вариантов ответа перемешивается.
3. **Ключ только на сервере.** Варианты ответа получают одноразовые
   идентификаторы попытки; «буква верного ответа» не существует, выучить
   шаблон вроде b-c-a невозможно.
4. **Повтор — другие семейства.** Семейства и варианты, уже показанные
   кандидату, при пересдаче отбираются в последнюю очередь.
5. **Контроль частоты показа.** Среди равных предпочитаются реже показанные
   варианты: банк расходуется равномерно, «засвеченных» вопросов меньше.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from product.validation_sim.bank import BankIndex
from product.validation_sim.scale import GRADES, next_grade, prev_grade

BLUEPRINT = {"below": 3, "at": 6, "above": 3}
TARGET_RADIUS = 0.6  # доп. блок: сложность вопроса не дальше ±0.6 от спорной границы


@dataclass
class Exposure:
    """Счётчик показов вариантов и семейств по всей платформе."""

    variants: dict[str, int] = field(default_factory=dict)
    families: dict[str, int] = field(default_factory=dict)

    def add(self, family_id: str, variant_id: str) -> None:
        self.variants[variant_id] = self.variants.get(variant_id, 0) + 1
        self.families[family_id] = self.families.get(family_id, 0) + 1


@dataclass
class History:
    """Что уже видел конкретный кандидат: для пересдачи без повторов."""

    families: set[str] = field(default_factory=set)
    variants: set[str] = field(default_factory=set)


def slot_plan(grade: str) -> list[tuple[str, str, int]]:
    """Список (позиция, уровень, сколько вопросов) для теста на grade."""
    below, above = prev_grade(grade), next_grade(grade)
    at = BLUEPRINT["at"]
    plan = []
    if below:
        plan.append(("below", below, BLUEPRINT["below"]))
    else:
        at += BLUEPRINT["below"]  # junior: вопросы «ниже» заменяются уровнем junior
    if not above:
        at += BLUEPRINT["above"]  # lead: вопросы «выше» заменяются уровнем lead
    plan.append(("at", grade, at))
    if above:
        plan.append(("above", above, BLUEPRINT["above"]))
    return plan


def _pick_families(
    pool: list[dict], count: int, history: History, exposure: Exposure, rng: random.Random,
    retired: frozenset[str] = frozenset(),
) -> list[dict]:
    """Сначала действующие (не выведенные после пилота), затем невиданные
    кандидатом, затем реже показанные; ничьи — случайно. Выведенные семейства
    берутся только если действующих не хватает на позицию плана."""
    ranked = sorted(
        pool,
        key=lambda fam: (
            fam["family_id"] in retired,
            fam["family_id"] in history.families,
            exposure.families.get(fam["family_id"], 0),
            rng.random(),
        ),
    )
    return ranked[:count]


def _pick_variant(family: dict, history: History, exposure: Exposure, rng: random.Random) -> dict:
    return min(
        family["variants"],
        key=lambda var: (
            var["variant_id"] in history.variants,
            exposure.variants.get(var["variant_id"], 0),
            rng.random(),
        ),
    )


def assemble_form(
    index: BankIndex,
    spec: str,
    grade: str,
    rng: random.Random,
    history: History,
    exposure: Exposure,
    retired: frozenset[str] = frozenset(),
) -> dict:
    """Собирает вариант. Возвращает словарь с ``items`` и серверным ``key``.

    ``key`` хранится только на сервере (в продукте — в записи попытки) и
    никогда не уходит клиенту; клиенту отдаётся ``public_view(form)``.
    """
    items = []
    key: dict[str, object] = {}
    for slot, level, count in slot_plan(grade):
        for family in _pick_families(index.families(spec, level), count, history, exposure, rng, retired):
            items.append(_make_item(index, family, slot, level, rng, history, exposure, key))
    rng.shuffle(items)
    return {"specialization": spec, "grade": grade, "items": items, "key": key}


def assemble_targeted(
    index: BankIndex,
    spec: str,
    grade: str,
    boundary: float,
    count: int,
    rng: random.Random,
    history: History,
    exposure: Exposure,
    retired: frozenset[str] = frozenset(),
) -> dict:
    """Дополнительный блок для пограничного результата (план улучшений, п. 2).

    Берутся ещё не виденные кандидатом семейства его специализации, чья
    сложность ближе всего к спорной границе грейдов: именно такие вопросы
    дают больше всего информации о том, по какую сторону границы кандидат.
    """
    pool = [
        fam
        for level in GRADES
        for fam in index.families(spec, level)
        if fam["family_id"] not in history.families
        and fam["family_id"] not in retired
        # Вопрос далеко от границы почти ничего не говорит о том, по какую
        # сторону от неё кандидат: такие не берём, блок может выйти короче.
        and abs(fam["b"] - boundary) <= TARGET_RADIUS
    ]
    pool.sort(key=lambda fam: (abs(fam["b"] - boundary), exposure.families.get(fam["family_id"], 0), rng.random()))
    key: dict[str, object] = {}
    items = [
        _make_item(index, fam, "extra", fam["level"], rng, history, exposure, key) for fam in pool[:count]
    ]
    return {"specialization": spec, "grade": grade, "items": items, "key": key}


def _make_item(
    index: BankIndex,
    family: dict,
    slot: str,
    level: str,
    rng: random.Random,
    history: History,
    exposure: Exposure,
    key: dict,
) -> dict:
    """Один вопрос попытки: выбор варианта, перемешивание, серверный ключ."""
    variant = _pick_variant(family, history, exposure, rng)
    kind = family["type"]
    space = int(index.types[kind]["space"])
    item = {
        "item_id": variant["variant_id"],
        "family_id": family["family_id"],
        "slot": slot,
        "level": level,
        "type": kind,
        "a": variant["a"],
        "b": variant["b"],
        "c": float(index.types[kind]["c"]),
        "space": space,
    }
    if kind in ("single", "judge") and family.get("sample"):
        # Перемешиваем варианты ответа и выдаём одноразовые id.
        sample = family["sample"]
        order = list(range(len(sample["options"])))
        rng.shuffle(order)
        # В продукте здесь secrets.token_hex: идентификатор непредсказуем.
        # В симуляции берём rng, чтобы прогон повторялся при том же seed.
        tokens = [f"{rng.getrandbits(32):08x}" for _ in order]
        item["options"] = [
            {"id": token, "text": sample["options"][src]} for token, src in zip(tokens, order)
        ]
        item["text"] = variant.get("text") or sample["text"]
        key[item["item_id"]] = tokens[order.index(sample["correct_index"])]
    else:
        # multi/order/numeric: свой текст и ответ варианта. Нет answer —
        # прежний случайный номер в пространстве ``space`` (повтор образца).
        item["text"] = variant.get("text") or (family["sample"] or {}).get("text", family["topic"])
        own = variant.get("answer")
        key[item["item_id"]] = own if own is not None else rng.randrange(space)
    history.families.add(family["family_id"])
    history.variants.add(variant["variant_id"])
    exposure.add(family["family_id"], variant["variant_id"])
    return item


def public_view(form: dict) -> list[dict]:
    """То, что видит кандидат: без ключа, без сложности, без позиции в плане."""
    visible = []
    for item in form["items"]:
        row = {"item_id": item["item_id"], "type": item["type"], "text": item["text"]}
        if "options" in item:
            row["options"] = [dict(option) for option in item["options"]]
        else:
            row["answer_space"] = item["space"]
        visible.append(row)
    return visible

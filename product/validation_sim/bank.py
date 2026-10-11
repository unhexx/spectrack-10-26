"""Банк вопросов: семейства изоморфных вариантов с параметрами сложности.

Почему так (ТЗ §2): «единый набор заданий для всех не подходит — задания и
ответы быстро разойдутся», а «генерация уникальных заданий нейросетью под
каждого кандидата не гарантирует сопоставимую сложность». Компромисс —
**семейство**: одна проверяемая тема с фиксированной сложностью и несколько
равноценных вариантов (другой контекст, другие значения в коде). Кандидаты
получают разные варианты, но одинаковые по трудности темы.

Структура ``data/bank.json``::

    {
      "schema_version": 1,
      "seed": 2026,
      "lower_bounds": {...},            # границы грейдов (scale.LOWER)
      "types": {"single": {"space": 3, "c": 0.333}, ...},
      "families": [
        {
          "family_id": "backend.middle.f01",
          "specialization": "backend",
          "level": "middle",            # грейд, к которому относится тема
          "type": "single",             # single | multi | order | numeric
          "topic": "...",               # короткое название темы
          "a": 1.2, "b": -0.55,          # заявленные параметры семейства
          "sample": {                   # образец формулировки из каталога
            "text": "...", "options": ["...", "...", "..."],
            "correct_index": 1, "source": "backend-middle-a/q1", "reused": false
          },
          "variants": [
            {"variant_id": "backend.middle.f01.v1", "a": 1.18, "b": -0.52, "text": null}
          ]
        }
      ]
    }

``variants[].text = null`` — своего текста нет, кабинет берёт образец.
Для multi, order и numeric ``build_bank`` ставит текст и ``answer`` из
``own_texts``: без этого ``live_view`` сбрасывает тип в judge.

Типы вопросов и вероятность угадать (``c``):
    single  — один верный из трёх (как сейчас в проекте): c = 1/3;
    multi   — несколько верных из пяти, засчитывается только точное множество: 1/31;
    order   — расставить 4 шага по порядку: 1/24;
    numeric — ввести число (результат фрагмента кода и т.п.): ~0.01.
Чем ниже c, тем меньше пользы от угадывания и от «шаблона ответов».
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from product.validation_sim.scale import GRADES, LOWER

SCHEMA_VERSION = 1

TYPES: dict[str, dict] = {
    "single": {"space": 3, "c": round(1 / 3, 4)},
    "multi": {"space": 31, "c": round(1 / 31, 4)},
    "order": {"space": 24, "c": round(1 / 24, 4)},
    "numeric": {"space": 100, "c": 0.01},
    # judge — «оцените каждый вариант: верно / неверно». Те же три варианта
    # ответа, что у single, но засчитывается только полностью верная разметка
    # (верный — «верно», оба неверных — «неверно»): 2^3 = 8 разметок, c = 1/8.
    # Так существующие 216 вопросов угадываются в 2.7 раза реже без новых текстов.
    "judge": {"space": 8, "c": 0.125},
}

# Тип каждого из 12 семейств пары «специализация × грейд»: 6 single, 2 multi,
# 2 order, 2 numeric. Половина вопросов — с низкой вероятностью угадать.
FAMILY_TYPES: tuple[str, ...] = (
    "single", "multi", "single", "order", "single", "numeric",
    "single", "multi", "single", "order", "single", "numeric",
)

FAMILIES_PER_PAIR = len(FAMILY_TYPES)
VARIANTS_PER_FAMILY = 5

DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "bank.json"


def _samples_by_pair() -> dict[tuple[str, str], list[dict]]:
    """Образцы формулировок из каталога проекта (216 вопросов).

    Импорт внутри функции: если пакет переносят в проект без
    ``product.matching.stems``, банк всё равно строится, только без образцов.
    """
    try:
        from product.matching.stems import catalog
    except ImportError:  # перенос в другую кодовую базу
        return {}
    pairs: dict[tuple[str, str], list[dict]] = {}
    for qset in catalog():
        key = (qset["specialization"], qset["grade"])
        for question in qset["questions"]:
            options = [choice["text"] for choice in question["choices"]]
            ids = [choice["id"] for choice in question["choices"]]
            pairs.setdefault(key, []).append(
                {
                    "text": question["text"],
                    "options": options,
                    "correct_index": ids.index(question["correct"]),
                    "source": f"{qset['set_id']}/{question['id']}",
                }
            )
    return pairs


def _specializations(samples: dict) -> list[str]:
    try:
        from product.matching.rules import load_rules

        return list(load_rules()["specializations"])
    except ImportError:
        found = sorted({spec for spec, _grade in samples})
        return found or ["backend", "frontend", "mobile", "data", "security", "devops"]


def build_bank(seed: int = 2026) -> dict:
    """Строит банк детерминированно по seed (один seed — один и тот же файл).

    Сложность: b семейства = нижняя граница его грейда + разброс N(0; 0.25),
    варианты семейства отличаются от него на N(0; 0.08) — изоморфы почти
    одинаковы по трудности, это и есть «сопоставимая сложность».
    Различающая сила a семейства ~ U(0.9; 1.6).
    """
    from product.validation_sim.own_texts import own_variant

    rng = random.Random(seed)
    samples = _samples_by_pair()
    families = []
    for spec in _specializations(samples):
        for grade in GRADES:
            pool = samples.get((spec, grade), [])
            for k in range(FAMILIES_PER_PAIR):
                family_id = f"{spec}.{grade}.f{k + 1:02d}"
                kind = FAMILY_TYPES[k]
                a = round(rng.uniform(0.9, 1.6), 3)
                b = round(LOWER[grade] + rng.gauss(0.0, 0.25), 3)
                sample = None
                if pool:
                    # 9 образцов на пару, семейств 12: последние три повторяют
                    # темы и помечены reused — их команде нужно дописать.
                    base = pool[k % len(pool)]
                    sample = dict(base, reused=k >= len(pool))
                topic = sample["text"][:70] if sample else f"Тема {k + 1}"
                variants = []
                for j in range(VARIANTS_PER_FAMILY):
                    variant = {
                        "variant_id": f"{family_id}.v{j + 1}",
                        "a": round(max(0.5, a + rng.gauss(0.0, 0.05)), 3),
                        "b": round(b + rng.gauss(0.0, 0.08), 3),
                        "text": None,
                    }
                    own = own_variant(spec, grade, k, j)
                    if own:
                        variant["text"] = own["text"]
                        variant["answer"] = own["answer"]
                    variants.append(variant)
                families.append(
                    {
                        "family_id": family_id,
                        "specialization": spec,
                        "level": grade,
                        "type": kind,
                        "topic": topic,
                        "a": a,
                        "b": b,
                        "sample": sample,
                        "variants": variants,
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "lower_bounds": dict(LOWER),
        "types": TYPES,
        "families": families,
    }


def save_bank(bank: dict, path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_bank(path: Path = DEFAULT_PATH) -> dict:
    bank = json.loads(Path(path).read_text(encoding="utf-8"))
    if bank.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"bank.json: ожидалась schema_version {SCHEMA_VERSION}")
    return bank


def live_view(bank: dict) -> dict:
    """Банк в том виде, в каком его сейчас может показать кабинет.

    - семейство со своим ``variants[].text`` сохраняет тип из банка;
    - без своего текста тип становится judge по образцу «один из трёх»;
    - семейства с повтором образца (``sample.reused``) не показываются, чтобы в
      одном варианте не встретились два одинаковых вопроса;
    - семейства без образца пропускаются.
    """
    families = []
    for family in bank["families"]:
        sample = family.get("sample")
        if not sample or sample.get("reused"):
            continue
        has_own_texts = any(v.get("text") for v in family["variants"])
        if not has_own_texts:
            family = dict(family, type="judge")
        families.append(family)
    return {**bank, "families": families}


class BankIndex:
    """Быстрый доступ к семействам по паре (специализация, уровень)."""

    def __init__(self, bank: dict) -> None:
        self.bank = bank
        self.types = bank["types"]
        self.by_pair: dict[tuple[str, str], list[dict]] = {}
        self.variant_family: dict[str, dict] = {}
        for family in bank["families"]:
            self.by_pair.setdefault((family["specialization"], family["level"]), []).append(family)
            for variant in family["variants"]:
                self.variant_family[variant["variant_id"]] = family

    def families(self, spec: str, level: str) -> list[dict]:
        return self.by_pair.get((spec, level), [])

    def all_variant_ids(self) -> list[str]:
        return sorted(self.variant_family)

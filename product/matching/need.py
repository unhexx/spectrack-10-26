"""Общий разбор потребности для золотого прогона и живой подборки.

Один проход даёт фильтр, кортеж порядка и фразу «почему этот человек»;
пустая потребность оставляет нули в кортеже и не выкидывает карточку
совпавшей категории.

ТЗ §2.1 «Описание потребности», «Результаты поиска ранжируются по степени соответствия… с краткой объяснимостью выдачи»; ТЗ §6 критерий 1.
"""

from __future__ import annotations

from snowballstemmer import stemmer

from product.matching.types import Need, NeedFit


def haystack_text(
    *,
    specialization: str,
    grade: str,
    stack: list[str],
    team_role: str,
    work_format: str,
    experience: str,
) -> str:
    return " ".join(
        [
            specialization,
            grade,
            " ".join(stack),
            team_role,
            work_format,
            experience or "",
        ]
    ).casefold()


def _stem_token(token: str, ru, en) -> str:
    if any("\u0400" <= ch <= "\u04ff" for ch in token):
        return ru.stemWord(token)
    return en.stemWord(token)


def need_tokens(query: str) -> list[str]:
    # Пара на вызов: у 3.1.1 курсор слова на объекте, а sync-ручки идут в threadpool.
    ru = stemmer("russian")
    en = stemmer("english")
    stems: list[str] = []
    seen_raw: set[str] = set()
    seen_stem: set[str] = set()
    for raw in str(query or "").split():
        token = raw.strip().casefold()
        if len(token) < 3 or token in seen_raw:
            continue
        seen_raw.add(token)
        stem = _stem_token(token, ru, en)
        if stem in seen_stem:
            continue
        seen_stem.add(stem)
        stems.append(stem)
    return stems


def _stem_set(haystack: str) -> set[str]:
    ru = stemmer("russian")
    en = stemmer("english")
    stems: set[str] = set()
    for raw in str(haystack or "").split():
        token = raw.strip().casefold()
        if len(token) < 3:
            continue
        stems.add(_stem_token(token, ru, en))
    return stems


def _as_need(need: Need | dict) -> Need:
    if isinstance(need, Need):
        return need
    return Need.from_mapping(need)


def evaluate_need(
    *,
    assigned_category_id: str,
    test_score: int | None,
    stack: list[str],
    team_role: str,
    work_format: str,
    fsp_count: int,
    haystack: str,
    need: Need | dict,
    rank_key: str = "",
) -> NeedFit:
    """Фильтр, порядок внутри категории и фраза «почему этот человек» — один проход.

    Пустая потребность оставляет нули в кортеже и не выкидывает карточку совпавшей категории.
    """
    spec = _as_need(need)
    empty = (0, 0, 0, 0, 0, rank_key)
    if assigned_category_id != spec.category_id:
        return NeedFit(False, empty, "")
    overlap = [item for item in stack if item in set(spec.stack)]
    if spec.stack and not overlap:
        return NeedFit(False, empty, "")
    if spec.team_role and team_role != spec.team_role:
        return NeedFit(False, empty, "")
    if spec.work_format and work_format != spec.work_format:
        return NeedFit(False, empty, "")
    if spec.has_fsp is True and fsp_count <= 0:
        return NeedFit(False, empty, "")
    if spec.has_fsp is False and fsp_count > 0:
        return NeedFit(False, empty, "")
    needles = need_tokens(spec.q)
    stem_set = _stem_set(haystack)
    hit = [token for token in needles if token in stem_set]
    if needles and len(hit) != len(needles):
        return NeedFit(False, empty, "")
    role_hit = 1 if spec.team_role and team_role == spec.team_role else 0
    format_hit = 1 if spec.work_format and work_format == spec.work_format else 0
    fsp_hit = 0
    if spec.has_fsp is True and fsp_count > 0:
        fsp_hit = 1
    elif spec.has_fsp is False and fsp_count <= 0:
        fsp_hit = 1
    parts = [f"Категория {assigned_category_id}"]
    if overlap:
        parts.append("в профиле стек " + ", ".join(overlap))
    if role_hit:
        parts.append(f"в профиле роль {team_role}")
    if format_hit:
        parts.append(f"в профиле формат {work_format}")
    if spec.has_fsp is True and fsp_count > 0:
        parts.append(f"в профиле {fsp_count} достижений ФСП")
    elif spec.has_fsp is False and fsp_count <= 0:
        parts.append("в профиле нет достижений ФСП")
    if hit:
        parts.append("в тексте профиля: " + ", ".join(hit))
    if len(parts) == 1:
        if test_score is None:
            parts.append("Грейд не подтверждён, потребность без уточнений")
        else:
            parts.append("Тест сдан, потребность без уточнений")
    return NeedFit(
        True,
        (len(overlap), role_hit, format_hit, fsp_hit, len(hit), rank_key),
        ". ".join(parts) + ".",
    )


_NEIGHBOR_FIELDS = (
    ("подтверждён", True),
    ("балл", False),
    ("число достижений", False),
    ("приз", True),
)


def _rank_fields(rank_key: str) -> tuple[int, int, int, int] | None:
    bits = str(rank_key).split("-")
    if len(bits) != 5:
        return None
    try:
        confirmed, score, count, prize, _profile_id = (int(bit) for bit in bits)
    except ValueError:
        return None
    return confirmed, score, count, prize


def _field_text(value: int, yesno: bool) -> str:
    if yesno:
        return "да" if value else "нет"
    return str(value)


def neighbor_phrase(
    sort_key: tuple[int, int, int, int, int, str],
    next_sort_key: tuple[int, int, int, int, int, str] | None,
) -> str:
    """Сравнение с соседней карточкой. Кортеж сортировки не меняет."""
    if next_sort_key is None:
        return "Ниже этой карточки в подборке никого нет."
    mine = _rank_fields(sort_key[5])
    other = _rank_fields(next_sort_key[5])
    if mine is None or other is None:
        return ""
    compared = ", ".join(
        f"{name} {_field_text(left, yesno)} против {_field_text(right, yesno)}"
        for (name, yesno), left, right in zip(_NEIGHBOR_FIELDS, mine, other, strict=True)
    )
    if sort_key[:5] != next_sort_key[:5]:
        return f"Выше соседней карточки по совпадению потребности: {compared}."
    for (name, yesno), left, right in zip(_NEIGHBOR_FIELDS, mine, other, strict=True):
        if left == right:
            continue
        if left > right:
            return (
                f"Выше соседней карточки по полю {name}: "
                f"{_field_text(left, yesno)} против {_field_text(right, yesno)}."
            )
        return (
            "Выше соседней карточки не по полям подтверждён, балл, "
            "число достижений и приз."
        )
    return "С соседней карточкой совпадают подтверждён, балл, число достижений и приз."


def matches_need(
    *,
    assigned_category_id: str,
    test_score: int | None,
    stack: list[str],
    team_role: str,
    work_format: str,
    fsp_count: int,
    haystack: str,
    need: Need | dict,
) -> bool:
    return evaluate_need(
        assigned_category_id=assigned_category_id,
        test_score=test_score,
        stack=stack,
        team_role=team_role,
        work_format=work_format,
        fsp_count=fsp_count,
        haystack=haystack,
        need=need,
    ).matched

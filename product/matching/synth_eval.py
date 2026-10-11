"""Контракт SYNTH-EVAL: слоты, банк имён и сборщик шарда.

Это не процедура теста из ТЗ. Кабинетный тест — банк семейств и IRT
в ``product.matching.assessment`` (ТЗ §2: грейд только по тесту).
Здесь итог не считается и в шард не пишется.

Старый тест из трёх вопросов (``select_template``, ``score_attempt``,
пороги 50 и 80) сюда не возвращается: он писал ожидаемый и фактический
итог из одной функции, и отчёт подтверждал сам себя. Буквы a/b/c в шарде —
след слота каталога, не сдача кабинета.

Ответы слота строит персона. Шарды в git пишет CLI;
тела шардов в этом модуле не хранятся.
"""

from __future__ import annotations

import json
import random
import sys
from functools import lru_cache
from pathlib import Path

from product.matching.category import assign_category
from product.matching.stems import catalog
from product.matching.types import ProfileView

SPECS = frozenset({"backend", "data", "devops", "frontend", "mobile", "security"})
GRADES = frozenset({"junior", "middle", "senior", "lead"})
LETTERS = ("a", "b", "c")
SEED = 20261010
N = 1000
SHARD_SIZE = 250
N_TEMPLATES = 72
PROFILE_ID_BASE = 10_001  # золотой набор 1..38 не пересекаем
RULE_VERSION = "v1"

_SYNTH_DIR = Path(__file__).resolve().parent / "golden" / "synth_eval"
_NAMES_PATH = _SYNTH_DIR / "names.json"
_WORK_FORMATS = ("офис", "удалённо", "гибрид")
_MOCK_FIO = frozenset(
    name.casefold().replace("ё", "е")
    for name in (
        "Иван Синтетиков",
        "Анна Синтетикова",
        "Мария Черновикова",
        "Пётр Черновой",
    )
)

FIRST_MALE = [
    "Александр", "Алексей", "Андрей", "Антон", "Артём",
    "Борис", "Вадим", "Валентин", "Василий", "Виктор",
    "Владимир", "Владислав", "Вячеслав", "Геннадий", "Георгий",
    "Григорий", "Денис", "Дмитрий", "Евгений", "Егор",
    "Иван", "Игорь", "Илья", "Кирилл", "Константин",
    "Леонид", "Максим", "Михаил", "Никита", "Николай",
    "Олег", "Павел", "Пётр", "Роман", "Сергей",
    "Станислав", "Степан", "Фёдор", "Юрий", "Ярослав",
]

FIRST_FEMALE = [
    "Александра", "Алина", "Алиса", "Анастасия", "Анна",
    "Валентина", "Валерия", "Вера", "Вероника", "Виктория",
    "Галина", "Дарья", "Диана", "Екатерина", "Елена",
    "Елизавета", "Зинаида", "Ирина", "Кристина", "Ксения",
    "Лариса", "Лидия", "Любовь", "Людмила", "Маргарита",
    "Марина", "Мария", "Надежда", "Наталья", "Нина",
    "Оксана", "Ольга", "Полина", "Светлана", "София",
    "Тамара", "Татьяна", "Ульяна", "Юлия", "Яна",
]

SURNAMES = {
    "m": [
        "Иванов", "Петров", "Смирнов", "Кузнецов", "Попов",
        "Соколов", "Лебедев", "Козлов", "Новиков", "Морозов",
        "Волков", "Алексеев", "Павлов", "Семёнов", "Голубев",
        "Виноградов", "Богданов", "Воробьёв", "Фёдоров", "Михайлов",
        "Белов", "Тарасов", "Беляев", "Комаров", "Орлов",
        "Киселёв", "Макаров", "Андреев", "Ковалёв", "Ильин",
        "Гусев", "Титов", "Кузьмин", "Куликов", "Баранов",
        "Соловьёв", "Зайцев", "Ефимов", "Данилов", "Жуков",
    ],
    "f": [
        "Иванова", "Петрова", "Смирнова", "Кузнецова", "Попова",
        "Соколова", "Лебедева", "Козлова", "Новикова", "Морозова",
        "Волкова", "Алексеева", "Павлова", "Семёнова", "Голубева",
        "Виноградова", "Богданова", "Воробьёва", "Фёдорова", "Михайлова",
        "Белова", "Тарасова", "Беляева", "Комарова", "Орлова",
        "Киселёва", "Макарова", "Андреева", "Ковалёва", "Ильина",
        "Гусева", "Титова", "Кузьмина", "Куликова", "Баранова",
        "Соловьёва", "Зайцева", "Ефимова", "Данилова", "Жукова",
    ],
}

EXPERIENCE = {
    ("backend", "junior"): "CRUD и миграции, код-ревью с наставником",
    ("backend", "middle"): "платежи и очереди, идемпотентность потребителя",
    ("backend", "senior"): "разбиение монолита, контракты сервисов и SLO",
    ("backend", "lead"): "архитектура платёжного контура, найм и менторство бэкенда",
    ("frontend", "junior"): "вёрстка форм, доступность и состояния загрузки",
    ("frontend", "middle"): "клиентский роутинг, кэш запросов и дизайн-система",
    ("frontend", "senior"): "сборка микрофронтов, бюджет бандла и SSR",
    ("frontend", "lead"): "стандарт UI-платформы, код-ревью и найм фронтенда",
    ("mobile", "junior"): "экраны списка и детали, локальный кэш",
    ("mobile", "middle"): "офлайн-очередь, пуши и навигация между модулями",
    ("mobile", "senior"): "модульный граф фич, релизный пайплайн стора",
    ("mobile", "lead"): "архитектура клиентских приложений, найм мобильной команды",
    ("data", "junior"): "витрины и SQL-отчёты, проверка качества витрины",
    ("data", "middle"): "пайплайны ETL, свежесть витрины и контракт колонок",
    ("data", "senior"): "оркестрация пайплайнов, контракт витрины и SLO",
    ("data", "lead"): "платформа данных, стандарты качества и найм аналитиков",
    ("security", "junior"): "ревью конфигураций, базовые проверки доступа",
    ("security", "middle"): "аудит linux-хостов, политики доступа и журналы",
    ("security", "senior"): "модель угроз сервиса, разбор инцидентов",
    ("security", "lead"): "программа безопасности продукта, найм и стандарты",
    ("devops", "junior"): "сборка образов, пайплайн и выкладка стейджа",
    ("devops", "middle"): "linux и docker, наблюдаемость и откаты",
    ("devops", "senior"): "кластер и сеть сервисов, бюджет ресурсов",
    ("devops", "lead"): "платформа выкладки, стандарты и найм эксплуатации",
}

CANONICAL_STACK = {
    "backend": "python",
    "frontend": "javascript",
    "mobile": "kotlin",
    "data": "python",
    "security": "linux",
    "devops": "linux",
}

_NEEDS_BY_SHARD = {
    1: ("backend-middle", "frontend-middle", "mobile-middle"),
    2: ("data-middle", "security-middle", "devops-middle"),
    3: ("backend-senior", "frontend-senior", "mobile-senior"),
    4: ("data-senior", "security-senior", "devops-senior"),
}

def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def template_id_of(row: dict) -> str:
    return str(row.get("template_id") or row.get("set_id") or "")


def load_template(template_id: str) -> dict:
    for row in catalog():
        if template_id_of(row) == template_id:
            return row
    raise KeyError(template_id)


def _catalog_templates() -> tuple[tuple[str, str, str], ...]:
    rows = []
    for row in catalog():
        template_id = template_id_of(row)
        letter = template_id.rsplit("-", 1)[-1]
        rows.append((str(row["specialization"]), str(row["grade"]), letter))
    return tuple(rows)


TEMPLATES = _catalog_templates()


def extra_copies(t: int) -> int:
    return 2 if t <= 63 else 1


def extras_for_shard(s: int) -> list[tuple[int, int]]:
    """Ascending t. At most one (t,k) per t on a given shard."""
    out = []
    for t in range(N_TEMPLATES):
        if extra_copies(t) == 2:
            if s == (t % 4) + 1:
                out.append((t, 3))
            if s == ((t + 1) % 4) + 1:
                out.append((t, 4))
        else:
            if s == (t % 4) + 1:
                out.append((t, 3))
    return out


def claim_persona(k: int, declared: str) -> tuple[str, str]:
    table = {
        0: ("aligned", "competent"),
        1: ("aligned", "competent"),
        2: ("aligned", "confirm"),
        3: ("stretch", "adjacent"),
        4: ("underclaim", "competent"),
    }
    kind, persona = table[k]
    if kind == "stretch" and declared == "junior":
        return "aligned", "competent"
    if kind == "underclaim" and declared == "lead":
        return "aligned", "confirm"
    return kind, persona


def neighbor_below(g: str) -> str:
    return {"middle": "junior", "senior": "middle", "lead": "senior"}[g]


def neighbor_above(g: str) -> str:
    return {"junior": "middle", "middle": "senior", "senior": "lead"}[g]


@lru_cache(maxsize=1)
def _chosen_names() -> tuple[str, ...]:
    data = json.loads(_NAMES_PATH.read_text(encoding="utf-8"))
    return tuple(data["chosen"])


def assign_slot(index: int) -> dict:
    assert 0 <= index < N
    names = _chosen_names()
    shard = index // SHARD_SIZE + 1
    offset = index % SHARD_SIZE
    if offset < 216:
        t, k = offset // 3, offset % 3
    else:
        t, k = extras_for_shard(shard)[offset - 216]
    spec, declared, letter = TEMPLATES[t]
    kind, persona = claim_persona(k, declared)
    if kind == "aligned":
        true_grade = declared
    elif kind == "stretch":
        true_grade = neighbor_below(declared)
    else:
        true_grade = neighbor_above(declared)
    return {
        "index": index,
        "shard": shard,
        "offset": offset,
        "t": t,
        "k": k,
        "template_id": f"{spec}-{declared}-{letter}",
        "specialization": spec,
        "declared_grade": declared,
        "grade": declared,
        "true_grade": true_grade,
        "claim_kind": kind,
        "persona": persona,
        "letter": letter,
        "display_name": names[index],
    }


def profile_id_for(spec: str, grade: str, letter: str, used: set[int], shard: int) -> int:
    """Луч на шарде с шагом 4. Старый выбор шаблона по хешу id сюда не входит."""
    pid = PROFILE_ID_BASE + (shard - 1)
    while pid in used:
        pid += 4
        if pid > PROFILE_ID_BASE + 2_000_000:
            raise RuntimeError(f"не нашли id для {spec}-{grade}-{letter}")
    used.add(pid)
    return pid


def forbidden_name(display_name: str) -> bool:
    folded = _fold(display_name)
    if "синтетик" in folded:
        return True
    if folded in _MOCK_FIO:
        return True
    if "fsp-synth-" in folded:
        return True
    if "кандидат 042" in folded:
        return True
    return any(
        token.isascii()
        and any(ch.isalpha() for ch in token)
        and all(ch.isalnum() or ch in "_-" for ch in token)
        for token in folded.split()
    )


def generate_chosen(first_male, first_female, surnames, seed=20261010) -> list[str]:
    pairs = [
        f"{first} {sur}"
        for first in first_male
        for sur in surnames["m"]
    ]
    pairs += [
        f"{first} {sur}"
        for first in first_female
        for sur in surnames["f"]
    ]
    rng = random.Random(seed)
    rng.shuffle(pairs)
    chosen: list[str] = []
    for name in pairs:
        if forbidden_name(name):
            continue
        chosen.append(name)
        if len(chosen) == 1000:
            break
    assert len(chosen) == 1000
    return chosen


def _adjacent_letter(correct: str) -> str:
    return min(letter for letter in LETTERS if letter != correct)


def answers_for(template: dict, persona: str, slot: int = 0) -> list[str]:
    questions = template["questions"]
    if persona == "competent":
        return [str(question["correct"]) for question in questions]
    if persona == "adjacent":
        return [_adjacent_letter(str(question["correct"])) for question in questions]
    wrong_i = slot % 3
    if persona == "confirm":
        answers = []
        for index, question in enumerate(questions):
            correct = str(question["correct"])
            answers.append(_adjacent_letter(correct) if index == wrong_i else correct)
        return answers
    if persona == "weak":
        answers = []
        for index, question in enumerate(questions):
            correct = str(question["correct"])
            answers.append(correct if index == wrong_i else _adjacent_letter(correct))
        return answers
    raise ValueError(persona)


def _need_row(need_id: str) -> dict:
    spec, grade = need_id.split("-", 1)
    return {
        "id": need_id,
        "category_id": f"{spec}:{grade}",
        "stack": [CANONICAL_STACK[spec]],
        "team_role": "",
        "work_format": "",
        "has_fsp": None,
        "q": "",
        "relevant_profile_ids": [],
    }


def template_pairs_for(shard: int) -> list[dict]:
    shard_grade = "middle" if shard in (1, 2) else "senior"
    pairs = []
    for spec in ("backend", "data", "devops", "frontend", "mobile", "security"):
        template_id = f"{spec}-{shard_grade}-a"
        template = load_template(template_id)
        answers = answers_for(template, persona="competent", slot=0)
        pairs.append(
            {
                "template_id": template_id,
                "answers": answers,
                "answers_repeat": list(answers),
            }
        )
    return pairs


def _profile_row(slot: dict, pid: int, display_name: str) -> dict:
    spec = slot["specialization"]
    declared = slot["declared_grade"]
    k = slot["k"]
    template = load_template(slot["template_id"])
    answers = answers_for(template, slot["persona"], slot=k)
    assigned = assign_category(
        ProfileView(
            profile_id=pid,
            specialization=spec,
            grade=declared,
            test_score=None,
            test_outcome=None,
            fsp_confirmed_count=k % 5,
        ),
        RULE_VERSION,
    )
    if declared in ("senior", "lead") and k % 2 == 0:
        team_role = "тимлид"
    else:
        team_role = "разработчик"
    return {
        "profile_id": pid,
        "display_name": display_name,
        "specialization": spec,
        "true_grade": slot["true_grade"],
        "grade": declared,
        "declared_grade": declared,
        "persona": slot["persona"],
        "claim_kind": slot["claim_kind"],
        "template_id": slot["template_id"],
        "rule_version": RULE_VERSION,
        "answers": answers,
        "test_score": None,
        "test_outcome": None,
        "expected_outcome": None,
        "expected_category_id": assigned.category_id,
        "stack": [CANONICAL_STACK[spec]],
        "team_role": team_role,
        "work_format": _WORK_FORMATS[k % 3],
        "experience": EXPERIENCE[(spec, declared)],
        "fsp_confirmed_count": k % 5,
    }


def build_shard(shard: int, *, names: list[str]) -> dict:
    if shard not in (1, 2, 3, 4):
        raise ValueError("shard")
    used: set[int] = set()
    profiles = []
    start = (shard - 1) * SHARD_SIZE
    for offset in range(SHARD_SIZE):
        index = start + offset
        slot = assign_slot(index)
        pid = profile_id_for(
            slot["specialization"],
            slot["declared_grade"],
            slot["letter"],
            used,
            shard,
        )
        display_name = names[offset]
        profiles.append(_profile_row(slot, pid, display_name))
    return {
        "shard": shard,
        "n": SHARD_SIZE,
        "seed": SEED,
        "rule_version": RULE_VERSION,
        "profiles": profiles,
        "needs": [_need_row(need_id) for need_id in _NEEDS_BY_SHARD[shard]],
        "template_pairs": template_pairs_for(shard),
    }


def load_shards(root: Path | None = None) -> dict:
    base = _SYNTH_DIR if root is None else Path(root)
    profiles: list[dict] = []
    needs: list[dict] = []
    pairs: list[dict] = []
    found: list[int] = []
    for shard in range(1, 5):
        path = base / f"shard-{shard}.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        found.append(shard)
        profiles.extend(data.get("profiles") or [])
        needs.extend(data.get("needs") or [])
        pairs.extend(data.get("template_pairs") or [])
    return {
        "shards": found,
        "n": len(profiles),
        "seed": SEED,
        "rule_version": RULE_VERSION,
        "profiles": profiles,
        "needs": needs,
        "template_pairs": pairs,
    }


def as_cases(bundle: dict) -> dict:
    return {
        "top_k": 3,
        "profiles": list(bundle["profiles"]),
        "needs": list(bundle["needs"]),
        "template_pairs": list(bundle["template_pairs"]),
    }


def write_shard(shard: int, *, root: Path | None = None) -> Path:
    base = _SYNTH_DIR if root is None else Path(root)
    base.mkdir(parents=True, exist_ok=True)
    chosen = list(_chosen_names())
    start = SHARD_SIZE * (shard - 1)
    payload = build_shard(shard, names=chosen[start : start + SHARD_SIZE])
    path = base / f"shard-{shard}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--build-shard"] and len(args) == 2:
        shard = int(args[1])
        if shard not in (1, 2, 3, 4):
            raise SystemExit("shard must be 1..4")
        write_shard(shard)
        return
    raise SystemExit("usage: python -m product.matching.synth_eval --build-shard N")


if __name__ == "__main__":
    main()

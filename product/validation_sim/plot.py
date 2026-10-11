"""Графики валидации теста по ``out/results.json``.

Нужен matplotlib (в образ продукта не входит, ставится отдельно):

    pip install matplotlib
    python -m product.validation_sim.plot            # перерисовать по готовому results.json

Цвета — первые четыре слота проверенной категориальной палитры (различимы при
дальтонизме): «Единый набор» оранжевый, «Банк без доборов» синий, «Платформа»
бирюзовый, «Платформа + форматы» жёлтый. Значения подписаны на столбцах —
график читается и без цвета.

ТЗ §3.4 «собственная процедура валидации решения и её результаты».
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # без окна: работает на сервере и в CI
import matplotlib.pyplot as plt  # noqa: E402

from product.validation_sim.scale import GRADES  # noqa: E402

OLD, NEW, LIVE, V2 = "#eb6834", "#2a78d6", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
OUT_DIR = Path(__file__).resolve().parent / "out"


def _style(ax, title: str, ylabel: str = "%") -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _bars(ax, labels, series, ylim=110) -> None:
    """Сгруппированные столбцы: series = [(значения, цвет), ...]."""
    count = len(series)
    width = 0.8 / count
    for k, (vals, color) in enumerate(series):
        offset = (k - (count - 1) / 2) * width
        bars = ax.bar([x + offset for x in range(len(labels))], vals, width=width - 0.03, color=color)
        for bar, value in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 1.2, f"{value:.0f}",
                    ha="center", va="bottom", fontsize=7, color=INK)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, ylim)


def plot(results: dict, out_dir: Path = OUT_DIR) -> Path:
    old, new, live, full = (results["systems"][k] for k in ("old", "new", "v2_live", "v2"))
    lm, fm = live["main"]["summary"], full["main"]["summary"]
    plt.rcParams["font.family"] = "DejaVu Sans"  # кириллица есть во всех сборках matplotlib
    fig, axes = plt.subplots(2, 3, figsize=(18, 10.5))
    fig.patch.set_facecolor("#fcfcfb")

    # 1. Точность и стабильность
    ax = axes[0][0]
    keys = [("exact_pct", "Грейд = истинный"), ("retest_same_pct", "Тот же грейд\nпри пересдаче"),
            ("overrated_pct", "Грейд завышен\n(меньше — лучше)")]
    _bars(ax, [t for _, t in keys], [
        ([old["accuracy"][k] for k, _ in keys], OLD),
        ([new["accuracy"][k] for k, _ in keys], NEW),
        ([lm[k]["mean"] for k, _ in keys], LIVE),
        ([fm[k]["mean"] for k, _ in keys], V2),
    ])
    _style(ax, "Точность и стабильность грейда")

    # 2. Результат с уверенностью: доля «уверенно» против их точности
    ax = axes[0][1]
    target = 100 * live["pilot"]["target_accuracy"]
    ax.axhline(target, color=MUTED, linewidth=1, linestyle="--")
    ax.text(103, target + 0.3, f"цель {target:g}%", fontsize=8, color=MUTED, va="bottom", ha="right")
    for block, summary, color, name, dy in ((live, lm, LIVE, "Платформа", -78), (full, fm, V2, "Платформа + форматы", 18)):
        curve = block["main"]["curve"]
        ax.plot([p["coverage_pct"] for p in curve], [p["accuracy_pct"] for p in curve], color=color, linewidth=2)
        cov, acc = summary["confident_share_pct"]["mean"], summary["confident_accuracy_pct"]["mean"]
        ax.scatter([cov], [acc], s=90, color=color, edgecolor="#fcfcfb", linewidth=2, zorder=3)
        ax.annotate(f"{name}: порог {block['pilot']['confident_threshold']}\n«уверенно» {cov:.0f}% кандидатов,"
                    f" верно {acc:.1f}%\n(5 прогонов: {summary['confident_accuracy_pct']['min']:.0f}–"
                    f"{summary['confident_accuracy_pct']['max']:.0f}%)",
                    (cov, acc), textcoords="offset points", xytext=(14, dy), fontsize=8, color=INK,
                    arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
    v1_exact = new["accuracy"]["exact_pct"]
    ax.scatter([100], [v1_exact], s=60, color=NEW, zorder=3)
    ax.annotate(f"Банк без доборов: грейд всем,\nверно {v1_exact:g}%", (100, v1_exact), textcoords="offset points",
                xytext=(-78, -30), fontsize=8, color=INK)
    ax.set_xlim(0, 105)
    ax.set_ylim(75, 100)
    ax.set_xlabel("доля кандидатов со статусом «уверенно», % (линия — порог 0.98…0.50)", color=MUTED, fontsize=9)
    _style(ax, "Точность среди «уверенно» (больше — лучше)")

    # 3. Уловки
    ax = axes[0][2]
    tricks = [("random", "Наугад"), ("always_b", "Всегда «b»"), ("pattern_bca", "Ключ\nb-c-a"),
              ("longest", "Длинный\nответ")]
    _bars(ax, [t for _, t in tricks], [
        ([old["tricks"][k]["pass_pct"] for k, _ in tricks], OLD),
        ([new["tricks"][k]["pass_pct"] for k, _ in tricks], NEW),
        ([live["tricks"][k]["pass_pct"] for k, _ in tricks], LIVE),
        ([full["tricks"][k]["pass_pct"] for k, _ in tricks], V2),
    ])
    _style(ax, "Уловки: доля «сдал» (меньше — лучше)")

    # 4. Утечка
    ax = axes[1][0]
    shares = list(old["leakage"]["pass_pct_by_share"])
    xs = [float(s) * 100 for s in shares]
    for block, color in ((old, OLD), (new, NEW), (live, LIVE), (full, V2)):
        ys = [block["leakage"]["pass_pct_by_share"][s] for s in shares]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=6)
    ax.scatter([0], [old["key_known_pass_pct"]], marker="X", s=80, color=OLD, zorder=3)
    ax.annotate("единый набор: известен ключ b-c-a", (0, old["key_known_pass_pct"]), textcoords="offset points",
                xytext=(8, -4), fontsize=8, color=MUTED)
    ax.annotate(f"без утечки: единый набор {old['leakage']['pass_pct_by_share'][shares[0]]:.0f}%, "
                f"платформа {live['leakage']['pass_pct_by_share'][shares[0]]:.0f}%",
                (0, 3), fontsize=8, color=INK)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{x:.0f}%" for x in xs], fontsize=9)
    ax.set_xlabel("доля утёкшего банка", color=MUTED, fontsize=9)
    ax.set_ylim(0, 105)
    _style(ax, "Утечка: сдали ступень выше своей (меньше — лучше)")

    # 5. Точность по истинному грейду
    ax = axes[1][1]
    _bars(ax, list(GRADES), [
        ([old["accuracy"]["exact_by_true_grade"][g] for g in GRADES], OLD),
        ([new["accuracy"]["exact_by_true_grade"][g] for g in GRADES], NEW),
        ([live["main"]["exact_by_true_grade"][g] for g in GRADES], LIVE),
        ([full["main"]["exact_by_true_grade"][g] for g in GRADES], V2),
    ])
    _style(ax, "Грейд = истинный, по истинному грейду")

    # 6. Матрица ошибок теста платформы (одна последовательная шкала)
    ax = axes[1][2]
    conf = live["main"]["confusion"]
    cols = [*GRADES, "none"]
    matrix = [[conf[g][c] for c in cols] for g in GRADES]
    peak = max(max(row) for row in matrix)
    ax.imshow(matrix, cmap="Greens", vmin=0, vmax=peak, aspect="auto")
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            ax.text(j, i, str(value), ha="center", va="center", fontsize=9,
                    color="white" if value > peak * 0.55 else INK)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([*GRADES, "не подтв."], fontsize=9)
    ax.set_yticks(range(len(GRADES)))
    ax.set_yticklabels(GRADES, fontsize=9)
    ax.set_xlabel("присвоенный грейд", color=MUTED, fontsize=9)
    ax.set_ylabel("истинный грейд", color=MUTED, fontsize=9)
    ax.set_title("Платформа: истинный × присвоенный грейд", loc="left", fontsize=11, color=INK, pad=10)
    ax.tick_params(colors=MUTED, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (OLD, NEW, LIVE, V2)]
    fig.legend(handles, ["Единый набор: 3 вопроса, общий ключ",
                         "Банк без доборов: 12 вопросов, IRT",
                         "Платформа: кабинет, judge и число",
                         "Платформа + форматы: полный банк"],
               loc="upper left", bbox_to_anchor=(0.01, 0.995), ncol=4, frameon=False, fontsize=9.5)
    fig.suptitle(f"{results['candidates']} синтетических кандидатов, seed {results['seed']}; "
                 f"«Платформа» — среднее 5 прогонов", x=0.99, y=0.962, ha="right", fontsize=9, color=MUTED)
    fig.tight_layout(rect=(0, 0, 1, 0.94), h_pad=3, w_pad=2.5)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "validation_charts.png"
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


if __name__ == "__main__":
    data = json.loads((OUT_DIR / "results.json").read_text(encoding="utf-8"))
    print(plot(data))

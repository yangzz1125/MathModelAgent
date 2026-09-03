from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scienceplots  # noqa: F401
import seaborn as sns
from matplotlib import font_manager

WORKSPACE = Path(r"E:\MathModelAgentPi\workspaces\387f2e0b2668")
RESULT = WORKSPACE / "results/q1/result.json"
SENSITIVITY = WORKSPACE / "results/q1/sensitivity.csv"
OUTPUT = Path(__file__).resolve().parent / "bakery_decision_dashboard"

BLUE = "#4477AA"
CORAL = "#EE6677"
GREEN = "#228833"
GOLD = "#CCBB44"
INK = "#263238"
GRID = "#DDE2E6"
REGIMES = [
    (0, 40, BLUE, "面粉稀缺", "仅生产蛋糕"),
    (40, 160, GREEN, "资源协同", "混合生产"),
    (160, 180, CORAL, "劳动稀缺", "仅生产面包"),
]


def solution(flour: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    bread = np.where(
        flour <= 40,
        0,
        np.where(flour <= 160, (2 * flour - 80) / 3, 80),
    )
    cake = np.where(
        flour <= 40,
        flour,
        np.where(flour <= 160, (160 - flour) / 3, 0),
    )
    value = 30 * bread + 40 * cake
    shadow = np.where(flour < 40, 40, np.where(flour < 160, 20 / 3, 0))
    return bread, cake, value, shadow


def style_axis(ax: plt.Axes) -> None:
    ax.grid(axis="y", color=GRID, linewidth=0.65)
    ax.set_axisbelow(True)
    ax.tick_params(top=False, right=False, colors="#53606A")
    ax.spines["left"].set_color("#AAB3BA")
    ax.spines["bottom"].set_color("#AAB3BA")
    sns.despine(ax=ax)


def add_regimes(ax: plt.Axes, *, labels: bool = False) -> None:
    for left, right, color, title, detail in REGIMES:
        ax.axvspan(left, right, color=color, alpha=0.045, linewidth=0)
        if labels:
            middle = (left + right) / 2
            ax.text(
                middle,
                0.97,
                f"{title}\n{detail}",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                color=color,
                fontsize=8.2,
                fontweight="semibold",
                linespacing=1.25,
            )


def main() -> None:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    metrics = {item["name"]: float(item["value"]) for item in result["metrics"]}
    with SENSITIVITY.open(encoding="utf-8-sig", newline="") as source:
        samples = list(csv.DictReader(source))

    flour = np.linspace(0, 180, 361)
    bread, cake, value, shadow = solution(flour)
    sample_f = np.array([float(row["flour_capacity"]) for row in samples])
    sample_v = np.array([float(row["max_profit"]) for row in samples])

    installed = {item.name for item in font_manager.fontManager.ttflist}
    font = next(
        name
        for name in ("Noto Serif SC", "Source Han Serif SC", "SimSun")
        if name in installed
    )

    with plt.style.context(["science", "no-latex", "bright"]):
        plt.rcParams.update(
            {
                "font.family": "serif",
                "font.serif": [font],
                "font.size": 9,
                "axes.labelsize": 9.5,
                "xtick.labelsize": 8,
                "ytick.labelsize": 8,
            }
        )
        fig = plt.figure(figsize=(7.2, 6.1), layout="constrained")
        grid = fig.add_gridspec(2, 2, height_ratios=(1.58, 1), hspace=0.14, wspace=0.12)
        ax_value = fig.add_subplot(grid[0, :])
        ax_mix = fig.add_subplot(grid[1, 0])
        ax_shadow = fig.add_subplot(grid[1, 1])

        # (a) Value function and baseline decision
        add_regimes(ax_value, labels=True)
        ax_value.plot(flour, value, color=INK, linewidth=2.15, zorder=3)
        ax_value.scatter(
            sample_f,
            sample_v,
            s=25,
            facecolor="white",
            edgecolor=INK,
            linewidth=0.9,
            zorder=4,
            label="指定容量核验点",
        )
        for breakpoint in (metrics["first_breakpoint"], metrics["second_breakpoint"]):
            point_value = solution(np.array([breakpoint]))[2][0]
            ax_value.axvline(breakpoint, color="#7C878E", linestyle="--", linewidth=0.8)
            ax_value.scatter(
                [breakpoint], [point_value], s=45, color=CORAL,
                edgecolor="white", linewidth=0.9, zorder=5,
            )
            ax_value.annotate(
                f"断点 {breakpoint:g}",
                xy=(breakpoint, point_value),
                xytext=(8 if breakpoint == 40 else -8, -22),
                textcoords="offset points",
                ha="left" if breakpoint == 40 else "right",
                color=CORAL,
                fontsize=8.2,
                arrowprops={"arrowstyle": "-", "color": CORAL, "lw": 0.7},
            )

        baseline_f = 100
        baseline_v = metrics["max_profit"]
        ax_value.scatter([baseline_f], [baseline_v], marker="D", s=50, color=GOLD, zorder=6)
        ax_value.annotate(
            "基准容量 100\n面包 40 · 蛋糕 20\n最大利润 2000",
            xy=(baseline_f, baseline_v),
            xytext=(16, -46),
            textcoords="offset points",
            fontsize=8.2,
            color=INK,
            linespacing=1.35,
            bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "#C7CFD4", "lw": 0.7},
            arrowprops={"arrowstyle": "-", "color": "#8C969D", "lw": 0.7},
            zorder=7,
        )
        ax_value.text(0.012, 0.97, "a", transform=ax_value.transAxes, va="top", fontweight="bold", fontsize=11)
        ax_value.set_ylabel("最大利润 $V(F)$")
        ax_value.set_xlim(0, 180)
        ax_value.set_ylim(0, 2780)
        ax_value.set_xticks([0, 40, 80, 100, 120, 160, 180])
        ax_value.set_yticks([0, 800, 1600, 2000, 2400])
        ax_value.legend(loc="lower right", frameon=False, fontsize=7.8)
        style_axis(ax_value)

        # (b) Optimal production mix
        add_regimes(ax_mix)
        ax_mix.plot(flour, bread, color=BLUE, linewidth=1.9, label="面包", zorder=3)
        ax_mix.plot(flour, cake, color=CORAL, linewidth=1.9, linestyle="--", label="蛋糕", zorder=3)
        ax_mix.fill_between(flour, 0, bread, color=BLUE, alpha=0.06)
        ax_mix.fill_between(flour, 0, cake, color=CORAL, alpha=0.045)
        ax_mix.scatter([100, 100], [40, 20], marker="D", s=26, color=GOLD, zorder=5)
        ax_mix.text(
            0.0, 1.025, "b  最优生产组合", transform=ax_mix.transAxes,
            va="bottom", fontweight="semibold", clip_on=False,
        )
        ax_mix.text(176, 77, "面包", color=BLUE, ha="right", va="top", fontsize=8)
        ax_mix.text(35, 42, "蛋糕", color=CORAL, ha="right", va="bottom", fontsize=8)
        ax_mix.set(xlabel="面粉容量 $F$", ylabel="最优日产量")
        ax_mix.set_xlim(0, 180)
        ax_mix.set_ylim(0, 88)
        ax_mix.set_xticks([0, 40, 100, 160, 180])
        ax_mix.set_yticks([0, 20, 40, 60, 80])
        style_axis(ax_mix)

        # (c) Flour shadow price
        add_regimes(ax_shadow)
        ax_shadow.step(flour, shadow, where="post", color=GREEN, linewidth=2.0, zorder=3)
        ax_shadow.fill_between(flour, 0, shadow, step="post", color=GREEN, alpha=0.08)
        ax_shadow.scatter([20, 100, 170], [40, 20 / 3, 0], s=27, color=GREEN, zorder=4)
        ax_shadow.text(
            0.0, 1.025, "c  面粉的局部边际价值", transform=ax_shadow.transAxes,
            va="bottom", fontweight="semibold", clip_on=False,
        )
        ax_shadow.text(20, 36.5, "40", color=GREEN, ha="center", va="top", fontsize=8)
        ax_shadow.text(100, 9.2, "$20/3$", color=GREEN, ha="center", va="bottom", fontsize=8)
        ax_shadow.text(169, 2.2, "0", color=GREEN, ha="center", va="bottom", fontsize=8)
        ax_shadow.set(xlabel="面粉容量 $F$", ylabel="影子价格")
        ax_shadow.set_xlim(0, 180)
        ax_shadow.set_ylim(-2, 44)
        ax_shadow.set_xticks([0, 40, 100, 160, 180])
        ax_shadow.set_yticks([0, 20 / 3, 20, 40], labels=["0", "$20/3$", "20", "40"])
        style_axis(ax_shadow)

        for suffix in (".png", ".pdf", ".svg"):
            options = {"dpi": 300} if suffix == ".png" else {}
            fig.savefig(OUTPUT.with_suffix(suffix), **options)
        plt.close(fig)


if __name__ == "__main__":
    main()

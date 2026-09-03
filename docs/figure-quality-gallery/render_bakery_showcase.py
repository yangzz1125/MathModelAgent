from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import scienceplots  # noqa: F401
from adjustText import adjust_text
from matplotlib import font_manager

SOURCE = Path(r"E:\MathModelAgentPi\workspaces\387f2e0b2668\results\q1\sensitivity.csv")
OUTPUT = Path(__file__).resolve().parent / "bakery_value_showcase"


def main() -> None:
    with SOURCE.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    x = [float(row["flour_capacity"]) for row in rows]
    y = [float(row["max_profit"]) for row in rows]

    installed = {item.name for item in font_manager.fontManager.ttflist}
    font = next(
        name
        for name in ("Noto Serif SC", "Source Han Serif SC", "SimSun")
        if name in installed
    )
    with plt.style.context(["science", "no-latex", "bright"]):
        plt.rcParams.update({"font.family": "serif", "font.serif": [font]})
        fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")

        regimes = [
            (0, 40, "#4477AA", "-", "o", "面粉约束主导"),
            (40, 160, "#228833", "--", "s", "双资源共同约束"),
            (160, 180, "#EE6677", "-.", "^", "劳动约束主导"),
        ]
        for left, right, color, linestyle, marker, label in regimes:
            points = [(capacity, profit) for capacity, profit in zip(x, y) if left <= capacity <= right]
            if left == 40 and (40.0, 1600.0) not in points:
                points.insert(0, (40.0, 1600.0))
            if right == 160 and (160.0, 2400.0) not in points:
                points.append((160.0, 2400.0))
            ax.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                color=color,
                linewidth=2.2,
                linestyle=linestyle,
                marker=marker,
                markersize=4.5,
                label=label,
                zorder=3,
            )
            ax.axvspan(left, right, color=color, alpha=0.035, zorder=0)

        ax.axvline(40, color="0.45", linestyle="--", linewidth=0.8, zorder=1)
        ax.axvline(160, color="0.45", linestyle="--", linewidth=0.8, zorder=1)
        labels = [
            ax.text(40, 1600, "断点 $F=40$", fontsize=9),
            ax.text(160, 2400, "断点 $F=160$", fontsize=9),
        ]
        adjust_text(
            labels,
            ax=ax,
            expand=(1.3, 1.5),
            force_text=(0.4, 0.6),
            arrowprops={"arrowstyle": "-", "color": "0.35", "lw": 0.7},
        )

        ax.text(17, 1050, "边际利润 40", color="#4477AA", fontsize=8.5, rotation=52)
        ax.text(88, 2050, "边际利润 $20/3$", color="#228833", fontsize=8.5, rotation=14)
        ax.text(168, 2220, "边际利润 0", color="#EE6677", fontsize=8.5, ha="center")
        ax.set_xlabel("面粉容量 $F$（单位/日）")
        ax.set_ylabel("最大利润 $V(F)$（利润单位/日）")
        ax.set_xlim(-5, 185)
        ax.set_ylim(-100, 2650)
        ax.set_xticks([0, 40, 80, 120, 160, 180])
        ax.set_yticks([0, 800, 1600, 2000, 2400])
        ax.grid(axis="y", color="0.85", linewidth=0.6, zorder=0)
        ax.legend(loc="lower right", frameon=False, ncol=1)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(top=False, right=False)

        for suffix in (".png", ".pdf", ".svg"):
            options = {"dpi": 300} if suffix == ".png" else {}
            fig.savefig(OUTPUT.with_suffix(suffix), **options)
        plt.close(fig)


if __name__ == "__main__":
    main()

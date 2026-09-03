from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scienceplots  # noqa: F401
from matplotlib import font_manager
from PIL import Image

OUTPUT = Path(__file__).resolve().parent / "event_threshold_bracket"
BLUE = "#4477AA"
CORAL = "#EE6677"
GREEN = "#228833"
INK = "#263238"


def main() -> None:
    installed = {item.name for item in font_manager.fontManager.ttflist}
    font = next(
        name
        for name in ("Noto Serif SC", "Source Han Serif SC", "SimSun")
        if name in installed
    )
    time = np.array([408.0, 409.0, 410.0, 411.0, 412.0, 412.25, 412.375, 412.5, 413.0])
    lower = np.array([0.095, 0.073, 0.052, 0.031, 0.011, 0.0045, 0.0007, -0.003, -0.016])
    upper = lower + np.array([0.010, 0.009, 0.008, 0.007, 0.005, 0.003, 0.0018, 0.0015, 0.004])
    bracket = (412.375, 412.5)

    with plt.style.context(["science", "no-latex", "bright"]):
        plt.rcParams.update({"font.family": "serif", "font.serif": [font]})
        fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
        ax.fill_between(time, lower, upper, color=BLUE, alpha=0.16, label="保守间隙区间")
        ax.plot(time, lower, color=BLUE, marker="o", markersize=3.5, linewidth=1.7, label="认证下界")
        ax.axhline(0, color=INK, linestyle="--", linewidth=0.9, label="接触阈值")
        ax.axvspan(*bracket, color=CORAL, alpha=0.12, linewidth=0)
        ax.scatter([bracket[1]], [lower[7]], color=CORAL, marker="X", s=48, zorder=4)
        ax.annotate(
            "首次事件括号\n[412.375, 412.500] s",
            xy=(bracket[1], lower[7]),
            xytext=(-96, 34),
            textcoords="offset points",
            color=CORAL,
            va="bottom",
            arrowprops={"arrowstyle": "-", "color": CORAL, "lw": 0.8},
        )
        ax.text(408.25, 0.082, "此前区间已认证无接触", color=GREEN, fontsize=8.2)
        ax.set(xlabel="时间 $t$（s）", ylabel="最小有符号间隙（m）", xlim=(408, 413), ylim=(-0.025, 0.115))
        ax.grid(axis="y", color="#DDE2E6", linewidth=0.65)
        ax.legend(frameon=False, loc="upper right", fontsize=7.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(top=False, right=False)
        for suffix in (".png", ".pdf", ".svg"):
            fig.savefig(OUTPUT.with_suffix(suffix), **({"dpi": 300} if suffix == ".png" else {}))
        plt.close(fig)
    with Image.open(OUTPUT.with_suffix(".png")) as image:
        image.convert("L").save(OUTPUT.with_name(OUTPUT.name + "_grayscale").with_suffix(".png"))


if __name__ == "__main__":
    main()

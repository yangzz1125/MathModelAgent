from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scienceplots  # noqa: F401
import seaborn as sns
from matplotlib import font_manager
from PIL import Image, ImageDraw

BLUE = "#4477AA"
CORAL = "#EE6677"
GREEN = "#228833"
GOLD = "#CCBB44"
INK = "#263238"
GREY = "#98A2A8"
GRID = "#DDE2E6"
NAMES = (
    "multi_line",
    "grouped_bar",
    "horizontal_ranking",
    "boxplot",
    "bubble_scatter",
    "stacked_area",
)


def configure_style() -> None:
    installed = {item.name for item in font_manager.fontManager.ttflist}
    fonts = [name for name in ("Noto Serif SC", "Source Han Serif SC", "SimSun") if name in installed]
    if fonts:
        plt.rcParams.update({"font.family": "serif", "font.serif": fonts})
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.labelsize": 9.5,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.unicode_minus": False,
        }
    )


def style_axis(ax: plt.Axes, *, grid: str = "y") -> None:
    ax.grid(axis=grid, color=GRID, linewidth=0.65)
    ax.set_axisbelow(True)
    ax.tick_params(top=False, right=False, colors="#53606A")
    sns.despine(ax=ax)


def save(fig: plt.Figure, output: Path, name: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".pdf", ".svg"):
        fig.savefig(output / f"{name}{suffix}", **({"dpi": 300} if suffix == ".png" else {}))
    plt.close(fig)


def render_multi_line(output: Path) -> None:
    x = np.arange(1, 9)
    series = {
        "方案 A": (55 + 5.2 * x, BLUE, "-", "o"),
        "方案 B": (51 + 5.7 * x, CORAL, "--", "s"),
        "方案 C": (58 + 4.1 * x, GREEN, "-.", "^"),
    }
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    for label, (values, color, linestyle, marker) in series.items():
        ax.plot(x, values, color=color, linestyle=linestyle, marker=marker, linewidth=1.8, markersize=4, label=label)
        offset = {"方案 A": 10, "方案 B": -12, "方案 C": 0}[label]
        ax.annotate(
            label,
            xy=(x[-1], values[-1]),
            xytext=(8, offset),
            textcoords="offset points",
            color=color,
            va="center",
            fontsize=8,
        )
    ax.set(xlabel="阶段", ylabel="性能得分", xlim=(1, 8.8), xticks=x)
    style_axis(ax)
    save(fig, output, "multi_line")


def render_grouped_bar(output: Path) -> None:
    categories = ["场景 1", "场景 2", "场景 3", "场景 4"]
    values = np.array([[68, 74, 79, 83], [64, 77, 82, 86], [71, 72, 76, 81]])
    labels = ["方法 A", "方法 B", "方法 C"]
    colors = [BLUE, CORAL, GREEN]
    x = np.arange(len(categories))
    width = 0.23
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    hatches = ["//", "..", "xx"]
    for index, (row, label, color) in enumerate(zip(values, labels, colors)):
        offset = (index - 1) * width
        bars = ax.bar(x + offset, row, width, color=color, label=label)
        for bar in bars:
            bar.set_hatch(hatches[index])
            bar.set_edgecolor("white")
            bar.set_linewidth(0.35)
        ax.bar_label(bars, padding=2, fontsize=7.2)
    ax.set(xlabel="测试场景", ylabel="得分", xticks=x, xticklabels=categories, ylim=(0, 96))
    ax.legend(frameon=False, ncol=3, loc="upper left")
    style_axis(ax)
    save(fig, output, "grouped_bar")


def render_horizontal_ranking(output: Path) -> None:
    labels = np.array(["方案 A", "方案 B", "方案 C", "方案 D", "方案 E", "方案 F"])
    values = np.array([73.5, 88.1, 79.6, 84.3, 76.8, 81.7])
    order = np.argsort(values)
    labels, values = labels[order], values[order]
    colors = [GREY] * (len(values) - 1) + [CORAL]
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    bars = ax.barh(labels, values, color=colors, height=0.62)
    bars[-1].set_hatch("///")
    bars[-1].set_edgecolor(INK)
    bars[-1].set_linewidth(0.7)
    ax.bar_label(bars, labels=[f"{value:.1f}" for value in values], padding=4, fontsize=8)
    ax.set(xlabel="综合得分", xlim=(0, 96))
    style_axis(ax, grid="x")
    save(fig, output, "horizontal_ranking")


def render_boxplot(output: Path) -> None:
    index = np.arange(36)
    frame = pd.DataFrame(
        {
            "误差": np.r_[
                4.5 + 1.2 * np.sin(index * 0.8),
                3.8 + 0.9 * np.sin(index * 0.65 + 0.4),
                5.2 + 1.5 * np.sin(index * 0.55 + 0.8),
            ],
            "模型": np.repeat(["模型 A", "模型 B", "模型 C"], len(index)),
        }
    )
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    sns.boxplot(data=frame, x="模型", y="误差", hue="模型", palette=[BLUE, GREEN, CORAL], width=0.52, legend=False, ax=ax)
    sns.stripplot(data=frame, x="模型", y="误差", color=INK, alpha=0.28, size=2.4, jitter=0.16, ax=ax)
    ax.set(xlabel="", ylabel="绝对误差")
    style_axis(ax)
    save(fig, output, "boxplot")


def render_bubble_scatter(output: Path) -> None:
    cost = np.array([18, 24, 29, 35, 42, 48, 55, 63])
    quality = np.array([62, 70, 68, 78, 82, 86, 85, 91])
    scale = np.array([40, 65, 52, 95, 130, 160, 115, 190])
    groups = np.array(["传统", "传统", "改进", "改进", "改进", "先进", "先进", "先进"])
    frame = pd.DataFrame({"成本": cost, "质量": quality, "规模": scale, "类型": groups})
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    sns.scatterplot(data=frame, x="成本", y="质量", size="规模", hue="类型", style="类型", markers={"传统": "o", "改进": "s", "先进": "^"}, sizes=(45, 230), palette=[GREY, BLUE, CORAL], alpha=0.78, edgecolor="white", linewidth=0.7, ax=ax)
    ax.annotate("高质量方案", xy=(63, 91), xytext=(-74, -8), textcoords="offset points", arrowprops={"arrowstyle": "-", "color": CORAL}, color=CORAL)
    ax.set(xlabel="成本", ylabel="质量得分", xlim=(14, 68), ylim=(58, 94))
    ax.legend(frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=7.2)
    style_axis(ax, grid="both")
    save(fig, output, "bubble_scatter")


def render_stacked_area(output: Path) -> None:
    time = np.arange(1, 9)
    source_a = np.array([38, 42, 44, 41, 39, 36, 33, 30])
    source_b = np.array([22, 24, 27, 31, 34, 37, 40, 42])
    source_c = np.array([12, 14, 13, 16, 18, 20, 22, 25])
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    layers = ax.stackplot(time, source_a, source_b, source_c, labels=["来源 A", "来源 B", "来源 C"], colors=[BLUE, GREEN, GOLD], alpha=0.82)
    for layer, hatch in zip(layers, ("//", "..", "xx")):
        layer.set_hatch(hatch)
        layer.set_edgecolor("white")
        layer.set_linewidth(0.25)
    total = source_a + source_b + source_c
    ax.plot(time, total, color=INK, linewidth=1.4, marker="o", markersize=3, label="总量")
    ax.text(time[-1] + 0.1, total[-1], f"总量 {total[-1]}", va="center", fontsize=8, color=INK)
    ax.set(xlabel="阶段", ylabel="构成数量", xlim=(1, 8.7), xticks=time, ylim=(0, 108))
    ax.legend(frameon=False, ncol=4, loc="upper left")
    style_axis(ax)
    save(fig, output, "stacked_area")


def contact_sheet(output: Path) -> None:
    images = [Image.open(output / f"{name}.png").convert("RGB") for name in NAMES]
    thumb_width, thumb_height = 900, 540
    margin, caption = 28, 48
    sheet = Image.new("RGB", (2 * thumb_width + 3 * margin, 3 * (thumb_height + caption) + 4 * margin), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (name, image) in enumerate(zip(NAMES, images)):
        image.thumbnail((thumb_width, thumb_height))
        column, row = index % 2, index // 2
        x = margin + column * (thumb_width + margin)
        y = margin + row * (thumb_height + caption + margin)
        sheet.paste(image, (x + (thumb_width - image.width) // 2, y))
        draw.text((x, y + thumb_height + 10), name.replace("_", " "), fill=INK)
    sheet.save(output / "basic_gallery.png")
    sheet.convert("L").save(output / "basic_gallery_grayscale.png")
    for image in images:
        image.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Render deterministic basic plot fixtures; not scientific evidence.")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    with plt.style.context(["science", "no-latex", "bright"]):
        configure_style()
        render_multi_line(args.output)
        render_grouped_bar(args.output)
        render_horizontal_ranking(args.output)
        render_boxplot(args.output)
        render_bubble_scatter(args.output)
        render_stacked_area(args.output)
    contact_sheet(args.output)


if __name__ == "__main__":
    main()

"""Render and validate the pinned publication figure stack without running a model."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scienceplots  # noqa: F401
import seaborn as sns
from adjustText import adjust_text
from PIL import Image, ImageStat

from pi.figure_quality import (
    DEFAULT_STYLE_STACK,
    REQUIRED_CHECKS,
    available_chinese_font,
    figure_evidence_errors,
    figure_reference_catalog,
    figure_stack_errors,
    validate_figure_specs,
)


class FigureStackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.font = available_chinese_font()

    def _style(self) -> None:
        if self.font:
            plt.rcParams.update({"font.family": "serif", "font.serif": [self.font]})

    def _save(self, fig: plt.Figure, root: Path, name: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for suffix in (".png", ".pdf", ".svg"):
            options = {"dpi": 300} if suffix == ".png" else {}
            fig.savefig(root / f"{name}{suffix}", **options)
        plt.close(fig)
        png = root / f"{name}.png"
        with Image.open(png) as image:
            self.assertGreaterEqual(min(image.size), 600)
            self.assertGreater(ImageStat.Stat(image.convert("L")).var[0], 20)
        self.assertTrue((root / f"{name}.pdf").read_bytes().startswith(b"%PDF-"))
        ET.parse(root / f"{name}.svg")

    def test_pinned_stack_and_chinese_font_are_available(self) -> None:
        self.assertEqual(figure_stack_errors("Chinese"), [])
        self.assertIn(self.font, ("Noto Serif SC", "Source Han Serif SC", "SimSun"))

    def test_official_apis_render_eight_basic_families(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            rng = np.random.default_rng(42)
            with plt.style.context(list(DEFAULT_STYLE_STACK)):
                self._style()

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                capacity = pd.DataFrame(
                    {"面粉容量": [0, 40, 160, 180], "最大利润": [0, 1600, 2400, 2400]}
                )
                sns.lineplot(
                    data=capacity, x="面粉容量", y="最大利润", marker="o", ax=ax
                )
                labels = [
                    ax.text(40, 1600, "断点 40"),
                    ax.text(160, 2400, "断点 160"),
                ]
                adjust_text(
                    labels,
                    ax=ax,
                    arrowprops={"arrowstyle": "-", "color": "0.4"},
                )
                self._save(fig, output, "sensitivity_line")

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                comparison = pd.DataFrame(
                    {
                        "方案": ["基准", "方案 A", "方案 B"],
                        "利润": [2000, 2180, 2250],
                        "标准差": [40, 55, 48],
                    }
                )
                sns.barplot(data=comparison, x="方案", y="利润", ax=ax, errorbar=None)
                ax.errorbar(
                    np.arange(3), comparison["利润"], yerr=comparison["标准差"],
                    fmt="none", color="0.25", capsize=3,
                )
                ax.set_ylim(bottom=0)
                self._save(fig, output, "comparison_bar")

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                actual = np.linspace(0, 100, 80)
                predicted = actual + rng.normal(0, 6, actual.size)
                samples = pd.DataFrame({"真实值": actual, "预测值": predicted})
                sns.scatterplot(data=samples, x="真实值", y="预测值", ax=ax, alpha=0.65)
                sns.regplot(
                    data=samples, x="真实值", y="预测值", ax=ax,
                    scatter=False, color="#D55E00",
                )
                ax.plot([0, 100], [0, 100], "--", color="0.4", label="理想线")
                ax.legend()
                self._save(fig, output, "scatter_fit")

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                groups = pd.DataFrame(
                    {
                        "误差": np.r_[rng.normal(-1, 5, 120), rng.normal(2, 7, 120)],
                        "模型": ["模型 A"] * 120 + ["模型 B"] * 120,
                    }
                )
                sns.histplot(
                    data=groups, x="误差", hue="模型", element="step",
                    stat="density", common_norm=False, ax=ax,
                )
                self._save(fig, output, "distribution")

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                sns.violinplot(data=groups, x="模型", y="误差", inner="quart", ax=ax)
                sns.stripplot(
                    data=groups, x="模型", y="误差", color="0.15",
                    alpha=0.25, size=2, ax=ax,
                )
                self._save(fig, output, "box_violin")

                fig, ax = plt.subplots(figsize=(6.0, 4.5), layout="constrained")
                matrix = pd.DataFrame(
                    [[1.0, 0.55, -0.2], [0.55, 1.0, 0.1], [-0.2, 0.1, 1.0]],
                    index=["利润", "面粉", "劳动"], columns=["利润", "面粉", "劳动"],
                )
                sns.heatmap(
                    matrix, annot=True, fmt=".2f", cmap="vlag", center=0,
                    square=True, ax=ax, cbar_kws={"label": "相关系数"},
                )
                self._save(fig, output, "heatmap")

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                iteration = np.arange(1, 41)
                gap = np.exp(-iteration / 8)
                ax.semilogy(iteration, gap, marker="o", markevery=5, label="最优间隙")
                ax.axhline(1e-2, linestyle="--", color="#D55E00", label="停止阈值")
                ax.set(xlabel="迭代次数", ylabel="最优间隙")
                ax.legend()
                self._save(fig, output, "convergence")

                fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
                cost = np.array([12, 15, 19, 24, 31, 40])
                quality = np.array([55, 68, 77, 84, 89, 92])
                ax.scatter(cost, quality, label="非支配方案")
                ax.plot(cost, quality, linewidth=1)
                ax.set(xlabel="成本", ylabel="质量得分")
                ax.legend()
                self._save(fig, output, "pareto")

            self.assertEqual(len(list(output.glob("*.png"))), 8)
            self.assertEqual(len(list(output.glob("*.pdf"))), 8)
            self.assertEqual(len(list(output.glob("*.svg"))), 8)

    def test_basic_gallery_renders_six_accessible_families(self) -> None:
        root = Path(__file__).resolve().parents[2]
        script = root / "docs/figure-quality-gallery/render_basic_gallery.py"
        names = (
            "multi_line",
            "grouped_bar",
            "horizontal_ranking",
            "boxplot",
            "bubble_scatter",
            "stacked_area",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            subprocess.run(
                [sys.executable, str(script), "--output", str(output)],
                cwd=root,
                check=True,
                timeout=120,
            )
            for name in names:
                with Image.open(output / f"{name}.png") as image:
                    self.assertGreaterEqual(min(image.size), 600)
                    self.assertGreater(ImageStat.Stat(image.convert("L")).var[0], 20)
                self.assertTrue((output / f"{name}.pdf").read_bytes().startswith(b"%PDF-"))
                ET.parse(output / f"{name}.svg")
            with Image.open(output / "basic_gallery_grayscale.png") as image:
                self.assertEqual(image.mode, "L")
                self.assertGreater(ImageStat.Stat(image).var[0], 20)

    def _valid_workspace(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        for directory in ("input", "code/q1", "results/q1", "figures/q1"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "input/problem.md").write_text("capacity problem", encoding="utf-8")
        with (root / "results/q1/sensitivity.csv").open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerows((("F", "V"), (0, 0), (40, 1600), (160, 2400)))
        (root / "code/q1/plot.py").write_text("# generator\n", encoding="utf-8")
        (root / "figures/q1/value.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        (root / "figures/q1/value.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="500"/>',
            encoding="utf-8",
        )
        image = Image.new("RGB", (1200, 700), "white")
        for x in range(100, 1100):
            image.putpixel((x, 350), (0, 80, 160))
        image.save(root / "figures/q1/value.png")
        problem = {
            "id": "q1",
            "inputs": ["input/problem.md"],
            "outputs": [
                "code/q1/plot.py", "results/q1/sensitivity.csv",
                "figures/q1/value.pdf", "figures/q1/value.svg",
                "figures/q1/value.png",
            ],
            "claims": [{"id": "q1.value"}],
            "figure_specs": [{
                "id": "q1.value-figure",
                "claim_ids": ["q1.value"],
                "purpose": "Show the capacity breakpoints.",
                "plot_family": "sensitivity-line",
                "reference_id": "trend-01-sensitivity",
                "panels": ["capacity response"],
                "primary_encoding": "position",
                "secondary_encoding": "color+marker",
                "required_annotations": ["capacity breakpoints"],
                "final_width": "full",
                "vector_path": "figures/q1/value.pdf",
                "preview_path": "figures/q1/value.png",
                "generator_path": "code/q1/plot.py",
                "data_paths": ["results/q1/sensitivity.csv"],
                "required_data_fields": ["F", "V"],
            }],
        }
        verification = {
            "figures": [{
                "path": "figures/q1/value.pdf",
                "preview_path": "figures/q1/value.png",
                "spec_id": "q1.value-figure",
                "reference_id": "trend-01-sensitivity",
                "claim_ids": ["q1.value"],
                "purpose": "Show the capacity breakpoints.",
                "plot_family": "sensitivity-line",
                "generator_path": "code/q1/plot.py",
                "data_paths": ["results/q1/sensitivity.csv"],
                "required_data_fields": ["F", "V"],
                "style_stack": [*DEFAULT_STYLE_STACK, "matplotlib"],
                "language": "Chinese",
                "checks": sorted(REQUIRED_CHECKS),
            }]
        }
        return problem, verification

    def test_network_candidate_pack_has_official_sources_and_license(self) -> None:
        root = Path(__file__).resolve().parents[2]
        pack = root / "pi/skills/mathmodel-figure-quality/references/network-candidates/seaborn"
        catalog = json.loads((pack / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["source"]["license"], "BSD-3-Clause")
        self.assertEqual(catalog["status"], "integrated_reference")
        self.assertFalse(catalog["evidence_eligible"])
        self.assertEqual(len(catalog["templates"]), 12)
        self.assertIn("Redistribution and use", (pack / "LICENSE.md").read_text(encoding="utf-8"))
        for item in catalog["templates"]:
            self.assertTrue(item["page_url"].startswith("https://seaborn.pydata.org/examples/"))
            self.assertTrue(item["source_url"].startswith("https://github.com/mwaskom/seaborn/"))
            preview = root / item["preview_path"]
            script = root / item["script_path"]
            with Image.open(preview) as image:
                image.verify()
                self.assertEqual(image.size, (295, 295))
            self.assertGreater(script.stat().st_size, 100)

    def test_reference_catalog_contains_31_readable_non_evidence_previews(self) -> None:
        catalog = figure_reference_catalog()
        self.assertEqual(len(catalog), 31)
        for reference in catalog.values():
            self.assertFalse(reference["evidence_eligible"])
            preview = Path(__file__).resolve().parents[2] / reference["preview_path"]
            with Image.open(preview) as image:
                image.verify()

    def test_figure_specs_require_known_reference_and_declared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            problem, _ = self._valid_workspace(Path(directory))
            self.assertEqual(validate_figure_specs(problem)[0]["id"], "q1.value-figure")
            problem["figure_specs"][0]["reference_id"] = "unknown-reference"  # type: ignore[index]
            with self.assertRaisesRegex(ValueError, "reference_id is unknown"):
                validate_figure_specs(problem)

    def test_figure_provenance_accepts_real_vector_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem, verification = self._valid_workspace(root)
            self.assertEqual(figure_evidence_errors(root, problem, verification), [])
            problem["outputs"].remove("code/q1/plot.py")
            self.assertIn(
                "figure_protocol: invalid plan figure_specs: q1.figure_specs[0] artifact is not declared in outputs: code/q1/plot.py",
                figure_evidence_errors(root, problem, verification),
            )

            problem["outputs"].append("code/q1/plot.py")
            problem["figure_specs"][0]["required_data_fields"] = ["missing_field"]  # type: ignore[index]
            verification["figures"][0]["required_data_fields"] = ["missing_field"]  # type: ignore[index]
            self.assertTrue(any(
                "required data fields absent" in error
                for error in figure_evidence_errors(root, problem, verification)
            ))

    def test_specialized_template_id_must_come_from_existing_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem, verification = self._valid_workspace(root)
            spec = problem["figure_specs"][0]  # type: ignore[index]
            spec["reference_id"] = "matrix-02-cv-roc"
            spec["plot_family"] = "cross-validation-roc"
            figure = verification["figures"][0]  # type: ignore[index]
            figure["reference_id"] = "matrix-02-cv-roc"
            figure["plot_family"] = "cross-validation-roc"
            figure["style_stack"] = ["specialized:cv-roc-ci"]
            self.assertEqual(figure_evidence_errors(root, problem, verification), [])
            figure["reference_id"] = "trend-01-sensitivity"
            errors = figure_evidence_errors(root, problem, verification)
            self.assertTrue(any("differs from plan spec" in error for error in errors))
            figure["reference_id"] = "matrix-02-cv-roc"
            figure["style_stack"] = ["specialized:invented-template"]
            self.assertTrue(any(
                "unknown specialized templates" in error
                for error in figure_evidence_errors(root, problem, verification)
            ))

    def test_figure_provenance_rejects_raster_only_cross_problem_and_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            problem, verification = self._valid_workspace(root)
            figure = verification["figures"][0]  # type: ignore[index]
            figure["path"] = "figures/q1/value.png"
            figure["data_paths"] = ["results/q2/data.csv", "skills/examples/demo.csv"]
            errors = figure_evidence_errors(root, problem, verification)
            self.assertTrue(any("not PDF/SVG" in error for error in errors))
            self.assertTrue(any("cross-problem" in error for error in errors))
            self.assertTrue(any("template/example" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

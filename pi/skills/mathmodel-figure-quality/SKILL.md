---
name: mathmodel-figure-quality
description: Pi overlay for publication-quality data figures in MathModelAgent scientific workflows. Routes ordinary plots to SciencePlots + Seaborn/Matplotlib, labels to adjustText, specialized plots to the existing template skill, and conceptual diagrams to DrawIO while preserving real-data provenance.
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob
---

# MathModel Figure Quality

Use this overlay together with `$MATHMODELAGENT_ROOT/skills/3coding-visual/SKILL.md`. It does not replace scientific judgment or supply result data.

## Design principles

Apply these before choosing cosmetic details:

1. State the reader's intended takeaway before selecting the plot family.
2. Every visible element must earn its ink; use grey and whitespace to create hierarchy.
3. Prefer position over color and color over size for primary comparisons.
4. Annotate the scientific insight, not merely the axes.
5. Encode important distinctions twice, such as color plus line style or marker.
6. Keep scales, regime colors and terminology consistent across linked panels.
7. Show values/differences directly when the reader would otherwise need mental arithmetic.
8. Remove decoration, redundant legends and grids that do not support reading.

## Non-negotiable rules

1. Every figure must support at least one declared claim. Do not draw for decoration or page count.
2. Read figure data from current workspace inputs/results. Never use simulated values from a template, preview, example, or `*_replica` output as evidence.
3. Ordinary figures use `import scienceplots` and a style context containing `science`, `no-latex`, and `bright`.
4. Chinese figures set the first available serif font from `Noto Serif SC`, `Source Han Serif SC`, `SimSun`. English figures inherit SciencePlots serif defaults.
5. Use Seaborn 0.13.2 axes-level APIs for standard statistical charts. Use Matplotlib inside the same style context for parametric, geometric, feasible-region, Pareto, or convergence structures.
6. Use `adjustText.adjust_text()` after all artists are created when two or more necessary data labels may collide. Do not label every ordinary point.
7. Output a vector master (`.pdf` or `.svg`), a `.png` preview at 300 DPI, and preferably the other vector format. Keep the generating script under `code/<problem-id>/`.
8. Do not put a large title inside the plot. The paper owns the caption. Axis labels and colorbars include units when defined.
9. Render the preview at final paper size. Check labels, legend, margins, clipping, grayscale distinction, and nonblank pixels.
10. Record every figure in `results/<problem-id>/verification.json.figures` using the contract below.

## Default setup

```python
from matplotlib import font_manager
import matplotlib.pyplot as plt
import scienceplots  # registers styles

styles = ["science", "no-latex", "bright"]
with plt.style.context(styles):
    if language == "Chinese":
        installed = {item.name for item in font_manager.fontManager.ttflist}
        family = next(
            name for name in ("Noto Serif SC", "Source Han Serif SC", "SimSun")
            if name in installed
        )
        plt.rcParams.update({"font.family": "serif", "font.serif": [family]})
    fig, ax = plt.subplots(figsize=(6.3, 3.8), layout="constrained")
    # draw from real workspace data
```

Use one-column width near 3.4 inches only for simple figures with short labels. Use 6.0--6.5 inches for Chinese labels, multi-series legends, matrices, or full-width paper figures. Verify readability after embedding; do not make text large merely to fill space.

## Composition level

Choose the smallest composition that completes the scientific explanation:

- Use one panel when one relationship is sufficient for the claim.
- Use a 2--3 panel narrative figure when linked quantities share an x-axis or decision context and the extra panels explain mechanism, validation, or trade-off. Typical combinations are objective + decision variables + shadow price; prediction + residual distribution; convergence + feasibility residual.
- Keep x limits, regime shading, baseline markers, colors, terminology, and panel labels consistent across linked panels.
- Do not make a dashboard from unrelated metrics, duplicate a table as a chart, or add panels solely to look sophisticated.

## Routing

Read `references/figure-routing.md`. Summary:

- line, bar, scatter, regression, distribution, box/violin, heatmap: Seaborn axes-level API;
- parametric piecewise functions, geometric trajectories, feasible regions, convergence histories, Pareto frontiers: Matplotlib;
- SHAP, ROC confidence bands, Taylor diagrams and other catalog matches: existing `mathmodel-figure-templates`, but replace all simulated data and captions;
- model structure, algorithm flow, data pipeline: `4drawio`, never this Skill.

Default-disabled: pie/donut, 3D bars, radar, dual-y axes, rainbow/jet colormaps, decorative gradients, chart shadows. Use one only when the scientific contract specifically requires that encoding and explain why a clearer chart is insufficient.

## Provenance contract

`verification.json` always contains `figures`; use `[]` when no scientific figure is warranted. Each entry has exactly:

```json
{
  "path": "figures/q1/value_function.pdf",
  "preview_path": "figures/q1/value_function.png",
  "spec_id": "q1.value-regimes",
  "reference_id": "trend-01-sensitivity",
  "claim_ids": ["q1.value_function"],
  "purpose": "Show the two regime breakpoints and marginal-value changes.",
  "plot_family": "parametric-line",
  "generator_path": "code/q1/plot_value.py",
  "data_paths": ["results/q1/sensitivity.csv"],
  "style_stack": ["science", "no-latex", "bright", "matplotlib"],
  "language": "Chinese",
  "checks": [
    "source_data_loaded",
    "vector_exported",
    "preview_rendered",
    "final_size_checked",
    "grayscale_checked",
    "labels_checked"
  ]
}
```

For a specialized catalog layout add `specialized:<template-id>` to `style_stack`. Copy only structure/code ideas into the current problem generator; do not emit paths under the Skill or a `*_replica` artifact.

`spec_id`, `reference_id`, claims, purpose, plot family, generator, data, vector, and preview must exactly match the Planner-owned `figure_specs` entry. The Host rejects substitutions even when the replacement looks better.

## Reference selection

Read `references/figure-reference-catalog.json` and its selected preview before drawing. It contains 30 curated structures across trend, comparison, distribution, relationship, matrix/evaluation, and narrative multi-panel families. Every catalog preview has `evidence_eligible=false`: it is a visual layout reference, never source data or scientific evidence.

The Planner selects the reference. The Worker may preserve its panel organization, hierarchy, direct-label pattern, baseline treatment, and redundant encodings, but must replace all values and scientific text with current workspace evidence. If the selected reference is scientifically inappropriate, do not silently switch it; produce no candidate and report the plan defect so it can be replanned.

## Network candidates

Additional official Seaborn examples live under `references/network-candidates/seaborn/` with their BSD-3-Clause license, source scripts, previews, source URLs, and scientific adaptation notes. Their `network-*` IDs are valid Planner `reference_id` values because adapted entries are registered in `figure-reference-catalog.json`.

The upstream scripts remain reference-only: do not run them unchanged for evidence. They load demo datasets and use upstream presentation defaults; use the registered purpose/avoid rules, then implement the selected structure with current workspace data, final-size layout, provenance, and grayscale-safe encodings.

## Visual review protocol

Perform at most three render-and-inspect rounds per figure:

1. **Round 1, enumerate:** list visible panels, labels, legend entries, annotations and data layers; confirm the intended takeaway and source variables are actually present.
2. **Round 2, evaluate:** check axis ranges/baselines, units, uncertainty meaning, raw-data visibility, direct insight annotation, redundant color encoding, panel consistency, whitespace, clipping and overlap. Name at least one possible improvement before deciding no edit is needed.
3. **Round 3, regressions:** after fixes, confirm the original issues are gone and no new layout or semantic defect appeared. Stop after this round; unresolved defects must be reported rather than hidden.

Always inspect both the color preview and a grayscale conversion at final paper width. A successful `savefig`, parser check or nonblank image is not visual acceptance.

## Before stopping

- Run the generator from workspace root.
- Re-read exported data and compare plotted values to result evidence.
- Parse SVG or check PDF header and size.
- Open/render PNG at final size; a successful `savefig` is not a visual check.
- Include figure paths and provenance in the problem report and claim evidence.
- Stop after current problem artifacts; never edit accepted upstream artifacts.

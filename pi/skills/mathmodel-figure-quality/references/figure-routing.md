# Figure Routing

Choose by the scientific question, not by appearance.

| Scientific purpose | Preferred API/layout | Required evidence | Avoid |
| --- | --- | --- | --- |
| Parameter sensitivity, time trend, convergence | `sns.lineplot` or `Axes.plot`; `fill_between` for a defined interval | x/y table, interval definition, stopping point or threshold when claimed | smooth interpolation unsupported by the model |
| First event, contact, threshold crossing | `event-01-threshold-bracket`; bound curves plus explicit threshold and bracket | certified safe prefix, finite event witness, lower/upper bound fields, event bracket and certificate id | sensitivity-line reference, samples without conservative bounds, unbounded search horizon |
| Compare methods/scenarios | `sns.barplot` with defined error bars, or point plot when zero baseline is irrelevant | category values, statistic, error definition and sample size | truncated bar baseline, 3D bars |
| Relationship and fit | `sns.scatterplot` plus `sns.regplot`, or identity line for predicted-vs-actual | individual observations, fit method, test-set distinction | hiding points behind an opaque fit line |
| Distribution | `sns.histplot`, `sns.ecdfplot`, `sns.boxplot`, `sns.violinplot` plus points when affordable | observations, group definition, bandwidth/normalization when used | decorative density without sample size |
| Correlation/confusion/parameter grid | `sns.heatmap` | matrix values, row/column semantics, center/scale meaning | `jet`; diverging map without meaningful midpoint |
| Parametric closed form | Matplotlib line/segments with necessary breakpoints via adjustText | exact nodes or sampled table plus closed form in report | labels directly on top of curves |
| Feasible region or geometry | Matplotlib patches/collections with equal aspect when geometry requires it | coordinates, units, predicates and boundary definitions | distorted aspect ratio |
| Pareto trade-off | Matplotlib scatter/line with dominated/non-dominated distinction | complete candidate set or declared sampling, dominance rule | connecting unordered points |
| Model performance with repeated folds | existing `cv-roc-ci` or `taylor-diagram` template structure | fold predictions/metrics and interval calculation | template simulation or copied example AUC |
| Explainability | existing `multiclass-shap-combo` structure | actual SHAP values and feature values | SHAP-looking chart without SHAP computation |
| Rich statistical comparison | paired raincloud or existing composite only when individual paired observations exist | raw paired records and pairing key | using simulated jitter as observations |
| Model/algorithm/data flow | `4drawio` | accepted method structure | using a data plotting library |

## Style stack

Default ordinary figure:

```python
with plt.style.context(["science", "no-latex", "bright"]):
    ...
```

Use `no-latex` for CJK and for deterministic cross-machine rendering. `ieee` and `nature` are overrides for a named target, not quality levels. Do not combine every style.

Seaborn works on Matplotlib axes inside this context. Prefer axes-level APIs so figure dimensions and multi-panel layout remain explicit. Avoid calling `sns.set_theme()` after entering the SciencePlots context because it overwrites style parameters; use individual API calls and explicit palettes only when scientifically needed.

## Annotation

Use direct `Axes.annotate(..., xytext=..., textcoords="offset points")` for one label. For multiple labels:

```python
from adjustText import adjust_text
texts = [ax.text(x, y, label) for x, y, label in required_labels]
adjust_text(texts, ax=ax, arrowprops={"arrowstyle": "-", "color": "0.4"})
```

Call `adjust_text` last, after limits and all plotted artists are final. Fix the random seed if jitter or stochastic placement is used.

## Accessibility

- `bright` is colorblind safe; also use line style/marker differences for important multi-series distinctions.
- Sequential data: `viridis` or `cividis`.
- Diverging data around a meaningful midpoint: `vlag`, `icefire`, `RdBu_r`, `PuOr`, or `BrBG`.
- Never use red/green as the only distinction.
- Check a grayscale preview; do not claim grayscale-safe based on color names alone.
- Use patterns or point/line encodings when black-and-white print is an actual delivery requirement.

## Final-size checks

Evaluate the figure at the width used in the paper, not only as a large standalone PNG:

- axis labels, ticks, legends and annotations remain readable;
- no data label intersects another label, point, axis title or figure edge;
- legend does not hide the main data;
- no unexplained empty panel or excessive margin;
- bar charts start at zero unless the axis break is explicit and justified;
- log scales, normalization and uncertainty bands are named in caption/report;
- vector file and PNG preview encode the same data and labels.

# Seaborn Network Candidates

Downloaded from the official [Seaborn example gallery](https://seaborn.pydata.org/examples/index.html) and [GitHub repository](https://github.com/mwaskom/seaborn). The retained upstream license is [`LICENSE.md`](LICENSE.md) (BSD-3-Clause).

Contents:

- `previews/`: official gallery thumbnails;
- `scripts/`: matching official example source files;
- `catalog.json`: source URL, local paths, scientific use/avoid rules, and required adaptations;
- `network_basic_gallery.png`: local contact sheet built by `build_gallery.py`.

The adapted `network-*` entries are registered in the runtime `figure-reference-catalog.json` and can be selected by the Planner. The retained upstream scripts are reference-only: they are not current-workspace data, are not scientific evidence, and must never be run unchanged for a paper result. A Worker must replace demo datasets, adapt labels and statistical semantics, add grayscale-safe redundant encodings, and verify at final paper size.

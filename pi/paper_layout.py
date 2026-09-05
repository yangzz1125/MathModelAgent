"""Versioned, opt-in CUMCM source-layout contract; visual review is still required."""

import re
from pathlib import Path

LAYOUT_VERSION = "cumcm-v2"
LAYOUT_SOURCES = {version: Path(__file__).parent / "latex" / f"{version}.tex"
                  for version in ("cumcm-v1", "cumcm-v2")}
LAYOUT_SOURCE = LAYOUT_SOURCES[LAYOUT_VERSION]


def paper_layout_policy(project: dict) -> str | None:
    if (str(project.get("competition", "")).upper() == "CUMCM"
            and str(project.get("language", "")).casefold() in {"chinese", "zh", "中文"}
            and str(project.get("paper_engine", "")).casefold() == "latex"):
        return LAYOUT_VERSION
    return None


def paper_layout_errors(workspace: Path, policy: str | None) -> list[str]:
    if not policy:
        return []
    if policy not in LAYOUT_SOURCES:
        return [f"paper_layout: unsupported layout contract {policy}"]
    paper = workspace / "paper"
    style = paper / "cumcm-layout.tex"
    if not style.is_file() or style.read_bytes() != LAYOUT_SOURCES[policy].read_bytes():
        return [f"paper_layout: copy pi/latex/{policy}.tex unchanged to paper/cumcm-layout.tex"]
    main = paper / "main.tex"
    if not main.is_file():
        return ["paper_layout: main.tex is required"]
    text = re.sub(r"(?<!\\)%[^\n]*", "", main.read_text(encoding="utf-8"))
    errors = []
    declaration = re.search(r"\\documentclass\s*\[([^]]+)\]\s*\{ctexart\}", text)
    if not declaration or "12pt" not in {v.strip() for v in declaration[1].split(",")}:
        errors.append("paper_layout: use documentclass[a4paper,12pt]{ctexart}")
    preamble = text.split(r"\begin{document}", 1)[0]
    if not re.search(r"\\input\s*\{cumcm-layout(?:\.tex)?\}", preamble):
        errors.append("paper_layout: input cumcm-layout.tex in the master preamble")
    body = text.split(r"\begin{document}", 1)[-1]
    if not re.search(r"\\papertitle\s*\{", body) or re.search(r"\\maketitle\b", body):
        errors.append("paper_layout: use papertitle instead of default maketitle")
    if len(re.findall(r"\\papercontents\b", body)) != 1 or re.search(r"\\tableofcontents\b", body):
        errors.append("paper_layout: use papercontents once to separate abstract, contents and body")
    return errors

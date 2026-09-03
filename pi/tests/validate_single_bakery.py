"""Independent oracle for the one-question parametric bakery workflow."""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path


def close(actual: float, expected: float, label: str) -> None:
    assert math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7), (
        label,
        actual,
        expected,
    )


def value(flour: float) -> float:
    if flour <= 40:
        return 40 * flour
    if flour <= 160:
        return (20 * flour + 4000) / 3
    return 2400


def validate(workspace: Path) -> None:
    result = json.loads(
        (workspace / "results" / "q1" / "result.json").read_text(encoding="utf-8")
    )
    assert result["problem_id"] == "q1" and result["status"] == "candidate"
    metrics = {item["name"]: float(item["value"]) for item in result["metrics"]}
    close(metrics["bread_units"], 40, "baseline bread")
    close(metrics["cake_units"], 20, "baseline cake")
    close(metrics["max_profit"], 2000, "baseline profit")
    close(metrics["first_breakpoint"], 40, "first breakpoint")
    close(metrics["second_breakpoint"], 160, "second breakpoint")

    expected_flour = [0, 20, 40, 70, 100, 130, 160, 180]
    with (workspace / "results" / "q1" / "sensitivity.csv").open(
        newline="", encoding="utf-8-sig"
    ) as source:
        rows = list(csv.DictReader(source))
    assert [float(row["flour_capacity"]) for row in rows] == expected_flour
    for row in rows:
        flour = float(row["flour_capacity"])
        close(float(row["max_profit"]), value(flour), f"profit F={flour}")
        bread = float(row["bread_units"])
        cake = float(row["cake_units"])
        assert bread >= -1e-8 and cake >= -1e-8
        assert 2 * bread + cake <= flour + 1e-7
        assert bread + 2 * cake <= 80 + 1e-7
        close(30 * bread + 40 * cake, value(flour), f"objective F={flour}")

    plan = json.loads((workspace / "execution_plan.json").read_text(encoding="utf-8"))
    assert plan["schema_version"] == 2 and len(plan["problems"]) == 1
    audit = json.loads(
        (workspace / "reports" / "PLAN_AUDIT.json").read_text(encoding="utf-8")
    )
    review = json.loads(
        (workspace / "reports" / "q1_SCIENTIFIC_REVIEW.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["verdict"] == review["verdict"] == "accept"

    claims = {claim["id"] for claim in plan["problems"][0]["claims"]}
    paper_plan = json.loads((workspace / "paper_plan.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (workspace / "paper" / "paper_manifest.json").read_text(encoding="utf-8")
    )
    assert {entry["claim_id"] for entry in paper_plan["coverage"]} == claims
    assert {entry["claim_id"] for entry in manifest["coverage"]} == claims

    figures = workspace / "figures" / "q1"
    assert any(
        path.is_file() and path.stat().st_size > 0
        and path.suffix.lower() in {".svg", ".png", ".pdf"}
        for path in figures.iterdir()
    )
    pdf = workspace / "paper" / "main.pdf"
    assert pdf.is_file() and pdf.stat().st_size > 0
    report = (workspace / "reports" / "VERIFY_REPORT.md").read_text(
        encoding="utf-8", errors="replace"
    )
    assert re.search(r"(?im)^\s*(?:#+\s*)?(?:conclusion|final status|result)?\s*[:：-]?\s*PASS\s*$", report)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_single_bakery.py WORKSPACE")
    validate(Path(sys.argv[1]).resolve())
    print("SINGLE_BAKERY_PASS")

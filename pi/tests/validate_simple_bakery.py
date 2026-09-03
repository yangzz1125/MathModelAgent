"""Independent standard-library oracle for the simple bakery black-box run."""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path


def metrics(workspace: Path, problem_id: str) -> dict[str, float]:
    path = workspace / "results" / problem_id / "result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["problem_id"] == problem_id and data["status"] == "pass", path
    return {str(item["name"]): float(item["value"]) for item in data["metrics"]}


def close(actual: float, expected: float, name: str) -> None:
    assert math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6), (
        name,
        actual,
        expected,
    )


def validate(workspace: Path) -> None:
    q1 = metrics(workspace, "q1")
    close(q1["bread_units"], 40.0, "q1 bread")
    close(q1["cake_units"], 20.0, "q1 cake")
    close(q1["max_profit"], 2000.0, "q1 profit")

    q2 = metrics(workspace, "q2")
    close(q2["bread_units"], 140.0 / 3.0, "q2 bread")
    close(q2["cake_units"], 50.0 / 3.0, "q2 cake")
    close(q2["max_profit"], 6200.0 / 3.0, "q2 profit")
    close(q2["profit_increase"], 200.0 / 3.0, "q2 increase")

    expected = {
        80.0: (80.0 / 3.0, 80.0 / 3.0, 5600.0 / 3.0),
        90.0: (100.0 / 3.0, 70.0 / 3.0, 5800.0 / 3.0),
        100.0: (40.0, 20.0, 2000.0),
        110.0: (140.0 / 3.0, 50.0 / 3.0, 6200.0 / 3.0),
        120.0: (160.0 / 3.0, 40.0 / 3.0, 6400.0 / 3.0),
    }
    with (workspace / "results" / "q3" / "sensitivity.csv").open(
        newline="", encoding="utf-8-sig"
    ) as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == len(expected), "q3 scenario count"
    for row in rows:
        flour = float(row["flour_capacity"])
        bread, cake, profit = expected[flour]
        close(float(row["labor_capacity"]), 80.0, f"q3 labor {flour}")
        close(float(row["bread_units"]), bread, f"q3 bread {flour}")
        close(float(row["cake_units"]), cake, f"q3 cake {flour}")
        close(float(row["max_profit"]), profit, f"q3 profit {flour}")

    figures = workspace / "figures" / "q3"
    assert figures.is_dir() and any(
        path.is_file() and path.stat().st_size > 0 and path.suffix.lower() in {".png", ".pdf", ".svg"}
        for path in figures.iterdir()
    ), "missing q3 chart"
    pdf = workspace / "paper" / "main.pdf"
    if not pdf.is_file():
        candidates = list((workspace / "paper").glob("*.pdf"))
        assert candidates, "missing paper PDF"
        pdf = candidates[0]
    assert pdf.stat().st_size > 0, "empty paper PDF"
    report = (workspace / "reports" / "VERIFY_REPORT.md").read_text(
        encoding="utf-8", errors="replace"
    )
    assert re.search(r"\bPASS\b", report, re.IGNORECASE), "verification did not pass"

    plan_path = workspace / "execution_plan.json"
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("schema_version") == 2:
            audit = json.loads(
                (workspace / "reports" / "PLAN_AUDIT.json").read_text(encoding="utf-8")
            )
            assert audit["verdict"] == "accept", "plan audit did not accept"
            for problem in plan["problems"]:
                review = json.loads(
                    (workspace / "reports" / f"{problem['id']}_SCIENTIFIC_REVIEW.json").read_text(
                        encoding="utf-8"
                    )
                )
                assert review["verdict"] == "accept", f"{problem['id']} not scientifically accepted"
            paper_plan = json.loads(
                (workspace / "paper_plan.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (workspace / "paper" / "paper_manifest.json").read_text(encoding="utf-8")
            )
            expected = {
                claim["id"]
                for problem in plan["problems"]
                for claim in problem["claims"]
            }
            assert {entry["claim_id"] for entry in paper_plan["coverage"]} == expected
            assert {entry["claim_id"] for entry in manifest["coverage"]} == expected


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_simple_bakery.py WORKSPACE")
    validate(Path(sys.argv[1]).resolve())
    print("SIMPLE_BAKERY_PASS")

#!/usr/bin/env python3
"""Summarize explicitly recorded benchmark outcomes, not inferred successes.

Input: JSONL, one independent case/run per line. Required: case_id,
execution_status. Optional: elapsed_seconds, hung (boolean), quality_passed
(boolean), runtime_metrics. Duplicate case_id is refused; use unique repeat IDs.
Token counts and assistant-message counts are observations, not billing totals
or exact provider request counts. Missing observations remain unknown.
"""
from __future__ import annotations
import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

TERMINAL = {"completed", "completed_with_warnings", "partial", "failed", "cancelled"}
SUCCESS = {"completed", "completed_with_warnings"}
VALID = TERMINAL | {"paused", "waiting", "running", "starting", "unknown"}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def distribution(values):
    if not values:
        return {"observations": 0, "median": None, "p90_nearest_rank": None}
    ordered = sorted(values)
    return {"observations": len(values), "median": statistics.median(ordered), "p90_nearest_rank": ordered[math.ceil(len(ordered) * 0.9) - 1]}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"]:
            raise ValueError("Every row needs a nonempty string case_id")
        if row["case_id"] in seen:
            raise ValueError("Duplicate case_id: " + row["case_id"])
        seen.add(row["case_id"])
        if row.get("execution_status") not in VALID:
            raise ValueError("Invalid execution_status for " + row["case_id"])
        for field in ("hung", "quality_passed"):
            if row.get(field) is not None and not isinstance(row[field], bool):
                raise ValueError(field + " must be boolean or null")
        if row.get("elapsed_seconds") is not None and not finite_number(row["elapsed_seconds"]):
            raise ValueError("elapsed_seconds must be finite and nonnegative")
        if row.get("runtime_metrics") is not None and not isinstance(row["runtime_metrics"], dict):
            raise ValueError("runtime_metrics must be an object")
    total = len(rows)
    completed = [row for row in rows if row["execution_status"] in SUCCESS]
    quality_observed = [row for row in rows if isinstance(row.get("quality_passed"), bool)]
    hang_observed = [row for row in rows if isinstance(row.get("hung"), bool)]
    fault_rows = [row for row in rows if (row.get("runtime_metrics") or {}).get("faults")]
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    def metrics_for(name):
        values = [(row.get("runtime_metrics") or {}).get(name) for row in rows]
        return distribution([value for value in values if finite_number(value)])
    token_totals = []
    for row in rows:
        metrics = row.get("runtime_metrics") or {}
        tokens = metrics.get("tokens")
        if metrics.get("token_usage_complete") is True and isinstance(tokens, dict):
            values = [tokens.get(name) for name in ("input", "output", "cacheRead", "cacheWrite")]
            if all(finite_number(value) for value in values):
                token_totals.append(sum(values))
    return {
        "schema_version": 1,
        "runs": total,
        "execution_completion_rate": ratio(len(completed), total),
        "validated_completion_count": sum(row["execution_status"] in SUCCESS and row.get("quality_passed") is True for row in rows),
        "quality_observations": len(quality_observed),
        "validated_completion_rate_all_runs": ratio(sum(row["execution_status"] in SUCCESS and row.get("quality_passed") is True for row in rows), total) if len(quality_observed) == total and total else None,
        "partial_delivery_count": sum(row["execution_status"] == "partial" or row.get("delivery_status") == "partial" for row in rows),
        "hang_rate_observed_runs": ratio(sum(row["hung"] for row in hang_observed), len(hang_observed)),
        "hang_observations": len(hang_observed),
        "completion_after_runtime_fault_rate": ratio(sum(row["execution_status"] in SUCCESS for row in fault_rows), len(fault_rows)),
        "fault_observations": len(fault_rows),
        "all_attempt_elapsed_seconds": distribution([row["elapsed_seconds"] for row in rows if finite_number(row.get("elapsed_seconds"))]),
        "successful_elapsed_seconds": distribution([row["elapsed_seconds"] for row in completed if finite_number(row.get("elapsed_seconds"))]),
        "workflow_prompts": metrics_for("prompts"),
        "assistant_message_count_proxy_not_exact_llm_calls": metrics_for("assistant_messages"),
        "restarts": metrics_for("restarts"),
        "observed_complete_usage_token_sum": distribution(token_totals),
        "notice": "Partial delivery is not completion. Unknown hang/quality/token measurements are not zero. This summary does not independently verify mathematics or model billing.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in args.jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        output = json.dumps(summarize(rows), indent=2, allow_nan=False)
        if args.out:
            args.out.write_text(output + "\n", encoding="utf-8")
        print(output)
    except (OSError, ValueError) as exc:
        parser.exit(2, "Invalid benchmark input: " + str(exc) + "\n")


if __name__ == "__main__":
    main()

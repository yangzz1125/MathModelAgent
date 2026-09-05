"""Explicit Host-side paper continuation; never resurrect a terminal task in place."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pi.paper_layout import paper_layout_policy
from pi.scientific_review import acceptance_chain_errors
from pi.staged_workflow import frozen_errors, validate_execution_plan, workspace_hashes


def prepare(source: Path, destination: Path) -> dict:
    original = (source / "project.json").read_bytes()
    project = json.loads(original)
    workflow = project.get("workflow") or {}
    if project.get("status") != "failed" or workflow.get("contract_version") != 3:
        raise ValueError("Paper continuation requires a terminal failed contract-v3 source")
    if workflow.get("current") not in {"paper_planning", "diagram", "writing", "verify"}:
        raise ValueError("Source has not reached the paper stages")
    if workflow.get("pending_transition"):
        raise ValueError("Source has an unfinished Host transition")
    ledger = json.loads((source / "planning/ledger.json").read_text(encoding="utf-8"))
    plan = validate_execution_plan(source)
    frozen = workflow.get("frozen") or {}
    if set(frozen) != {problem["id"] for problem in plan["problems"]}:
        raise ValueError("Not every scientific problem is frozen")
    errors = frozen_errors(source, frozen) + acceptance_chain_errors(
        source, plan, contract_version=3, ledger=ledger,
    )
    if errors:
        raise ValueError("; ".join(errors))
    before = workspace_hashes(source)
    destination.mkdir(parents=True, exist_ok=False)
    # Only validated scientific/planning inputs enter the new program-owned task.
    for directory in ("input", "planning", "code", "results", "figures", "reports"):
        shutil.copytree(source / directory, destination / directory)
    (destination / "reports/PAPER_PLAN.md").unlink(missing_ok=True)
    (destination / "reports/VERIFY_REPORT.md").unlink(missing_ok=True)
    (destination / "reports/DRAWIO_REPORT.md").unlink(missing_ok=True)
    for filename in ("input_manifest.json", "execution_plan.json"):
        shutil.copy2(source / filename, destination / filename)
    (destination / "paper").mkdir()
    if (source / "project.json").read_bytes() != original or workspace_hashes(source) != before:
        raise ValueError("Source changed while importing; do not start continuation")
    errors = frozen_errors(destination, frozen) + acceptance_chain_errors(
        destination, validate_execution_plan(destination), contract_version=3, ledger=ledger,
    )
    if errors:
        raise ValueError("Imported evidence failed validation: " + "; ".join(errors))
    now = datetime.now(timezone.utc).isoformat()
    continued = {key: project[key] for key in (
        "schema_version", "source_folder", "problem_file", "competition", "language", "paper_engine",
        "model", "thinking", "planner_model", "planner_thinking", "worker_model", "worker_thinking",
        "user_notes", "user_requirements_file",
    ) if key in project}
    continued.update(project_id=destination.name, status="paused", created_at=now, started_at=now,
                     pause_reason="user_authorized_paper_continuation")
    continued["continuation_source"] = {
        "project_id": source.name, "status": "failed", "restart_stage": "paper_planning",
        "source_project_sha256": hashlib.sha256(original).hexdigest(),
        "completeness_sha256": hashlib.sha256((source / "reports/PLAN_COMPLETENESS.json").read_bytes()).hexdigest(),
        "imported_at": now,
    }
    current = copy.deepcopy(workflow)
    for field in ("review_snapshot", "last_review", "paper_visual_evidence", "document_review",
                  "verify_repair_count", "spike_elapsed_seconds", "supplemental_spike_ids"):
        current.pop(field, None)
    current.update(current="paper_planning", mode="run", supplemental_spike=False,
                   paper_layout=paper_layout_policy(project))
    for phase in current["phases"]:
        if phase["id"] in {"paper_planning", "diagram", "writing", "verify"}:
            phase_id, label = phase["id"], phase["label"]
            phase.clear()
            phase.update(id=phase_id, label=label, status="pending", attempts=0, last_error="",
                         started_at=None, completed_at=None)
            if phase_id == "paper_planning":
                phase.update(status="paused", status_before_pause="running", attempts=1, started_at=now)
    current["stage_snapshot"] = workspace_hashes(destination)
    continued["workflow"] = current
    (destination / "project.json").write_text(json.dumps(continued, ensure_ascii=False, indent=2), encoding="utf-8")
    return continued


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id")
    parser.add_argument("--start", action="store_true", help="Start through the existing loopback Bridge resume API")
    args = parser.parse_args()
    if len(args.source_id) != 12 or any(c not in "0123456789abcdef" for c in args.source_id):
        parser.error("source_id must be a 12-character lowercase hexadecimal task ID")
    root = Path(__file__).resolve().parents[1] / "workspaces"
    task_id = uuid.uuid4().hex[:12]
    prepare(root / args.source_id, root / task_id)
    print(json.dumps({"task_id": task_id, "source_id": args.source_id, "stage": "paper_planning"}), flush=True)
    if args.start:
        with urlopen(Request(f"http://127.0.0.1:8000/modeling/{task_id}/resume", data=b"", method="POST"), timeout=30) as response:
            print(response.read().decode("utf-8"), flush=True)


if __name__ == "__main__":
    main()

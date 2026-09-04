"""Deterministic contracts and prompts for the staged Pi workflow."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from pi.figure_quality import validate_figure_specs
from pi.scientific_review import (
    ScientificContractError,
    candidate_errors,
    validate_problem_science,
)

PROBLEM_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
FINAL_PHASES = (
    ("diagram", "流程与架构图"),
    ("writing", "竞赛论文撰写"),
    ("verify", "文档验证和验收"),
)
V2_FINAL_PHASES = (
    ("paper_planning", "论文内容规划"),
    *FINAL_PHASES,
)
V3_FINAL_PHASES = V2_FINAL_PHASES
EVIDENCE_LEVELS = {"A_certified", "B_bounded_numerical", "C_exploratory"}


class ContractError(ValueError):
    """A generated workflow artifact violates the local contract."""


def relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("path must be a non-empty string")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"unsafe relative path: {value}")
    return path.as_posix()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"missing {label}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label} is invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def inventory_path(workspace: Path, version: int) -> Path:
    return workspace / "planning" / "inventory" / f"v{version}" / "problem_inventory.json"


def validate_problem_inventory(workspace: Path, version: int) -> dict[str, Any]:
    path = inventory_path(workspace, version)
    raw = _file_object(path, path.relative_to(workspace).as_posix())
    if set(raw) != {"schema_version", "problems"} or raw.get("schema_version") != 1:
        raise ContractError("problem inventory requires schema_version=1 and problems only")
    problems = raw.get("problems")
    if not isinstance(problems, list) or not problems or len(problems) > 20:
        raise ContractError("problem inventory problems must contain 1..20 entries")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    output_ids: set[str] = set()
    for index, raw_problem in enumerate(problems):
        if not isinstance(raw_problem, dict):
            raise ContractError(f"inventory problems[{index}] must be an object")
        expected_problem_keys = {
            "id", "label", "depends_on", "requested_outputs", "input_paths",
            "interpretation", "ambiguities", "suggested_evidence_level",
        }
        if set(raw_problem) != expected_problem_keys:
            raise ContractError(f"inventory problems[{index}] keys mismatch")
        problem_id = str(raw_problem.get("id") or "")
        if not PROBLEM_ID_RE.fullmatch(problem_id) or problem_id in seen:
            raise ContractError(f"invalid or duplicate inventory problem id: {problem_id!r}")
        label = str(raw_problem.get("label") or "").strip()
        interpretation = str(raw_problem.get("interpretation") or "").strip()
        if not label or not interpretation:
            raise ContractError(f"{problem_id} requires label and interpretation")
        dependencies = _string_list(
            raw_problem.get("depends_on", []),
            f"{problem_id}.depends_on",
            allow_empty=True,
        )
        unknown = [dependency for dependency in dependencies if dependency not in seen]
        if unknown or len(set(dependencies)) != len(dependencies):
            raise ContractError(f"{problem_id} has invalid dependency order: {unknown}")
        requested_raw = raw_problem.get("requested_outputs")
        if not isinstance(requested_raw, list) or not requested_raw:
            raise ContractError(f"{problem_id}.requested_outputs must be non-empty")
        requested = []
        for output_index, raw_output in enumerate(requested_raw):
            if not isinstance(raw_output, dict) or set(raw_output) != {"id", "statement"}:
                raise ContractError(f"{problem_id}.requested_outputs[{output_index}] is invalid")
            output_id = str(raw_output.get("id") or "")
            statement = str(raw_output.get("statement") or "").strip()
            if not re.fullmatch(r"^[a-z][a-z0-9_.-]{0,63}$", output_id) or output_id in output_ids:
                raise ContractError(f"invalid or duplicate requested output id: {output_id!r}")
            if not statement:
                raise ContractError(f"{output_id}.statement is empty")
            output_ids.add(output_id)
            requested.append({"id": output_id, "statement": statement})
        inputs = [
            relative_path(value)
            for value in _string_list(raw_problem.get("input_paths"), f"{problem_id}.input_paths")
        ]
        for input_path_value in inputs:
            if not (
                input_path_value == "input_manifest.json"
                or input_path_value.startswith("input/")
            ):
                raise ContractError(f"{problem_id} inventory input is outside input/: {input_path_value}")
            if not (workspace / input_path_value).exists():
                raise ContractError(f"{problem_id} inventory input does not exist: {input_path_value}")
        ambiguities_raw = raw_problem.get("ambiguities")
        if not isinstance(ambiguities_raw, list):
            raise ContractError(f"{problem_id}.ambiguities must be a list")
        ambiguities = []
        ambiguity_ids: set[str] = set()
        for ambiguity_index, raw_ambiguity in enumerate(ambiguities_raw):
            if not isinstance(raw_ambiguity, dict) or set(raw_ambiguity) != {
                "id", "interpretations", "impact", "resolution_needed"
            }:
                raise ContractError(f"{problem_id}.ambiguities[{ambiguity_index}] is invalid")
            ambiguity_id = str(raw_ambiguity.get("id") or "")
            if not re.fullmatch(r"^[a-z][a-z0-9_.-]{0,63}$", ambiguity_id) or ambiguity_id in ambiguity_ids:
                raise ContractError(f"invalid or duplicate ambiguity id: {ambiguity_id!r}")
            ambiguity_ids.add(ambiguity_id)
            ambiguities.append({
                "id": ambiguity_id,
                "interpretations": _string_list(
                    raw_ambiguity.get("interpretations"),
                    f"{ambiguity_id}.interpretations",
                ),
                "impact": str(raw_ambiguity.get("impact") or "").strip(),
                "resolution_needed": str(raw_ambiguity.get("resolution_needed") or "").strip(),
            })
            if not ambiguities[-1]["impact"] or not ambiguities[-1]["resolution_needed"]:
                raise ContractError(f"{ambiguity_id} requires impact and resolution_needed")
        evidence_level = str(raw_problem.get("suggested_evidence_level") or "")
        if evidence_level not in EVIDENCE_LEVELS:
            raise ContractError(f"{problem_id}.suggested_evidence_level is invalid")
        normalized.append({
            "id": problem_id,
            "label": label,
            "depends_on": dependencies,
            "requested_outputs": requested,
            "input_paths": inputs,
            "interpretation": interpretation,
            "ambiguities": ambiguities,
            "suggested_evidence_level": evidence_level,
        })
        seen.add(problem_id)
    return {"schema_version": 1, "problems": normalized}


def method_version_dir(workspace: Path, problem_id: str, version: int) -> Path:
    return workspace / "planning" / "methods" / problem_id / f"v{version}"


def method_spec_hash(card: dict[str, Any]) -> str:
    problem = card["problem"]
    claim_contracts = [
        {
            key: value
            for key, value in claim.items()
            if key != "statement"
        }
        for claim in problem["claims"]
    ]
    normalized_failure_semantics = []
    for failure in card["problem"]["failure_semantics"]:
        item = dict(failure)
        item.pop("classification", None)
        normalized_failure_semantics.append(item)
    return canonical_hash({
        "method": problem["method"],
        "inputs": problem["inputs"],
        "outputs": problem["outputs"],
        "validation": problem["validation"],
        "runtime_limit_seconds": problem["runtime_limit_seconds"],
        "requested_outputs": problem["requested_outputs"],
        "interpretation": problem["interpretation"],
        "assumptions": problem["assumptions"],
        "claims": claim_contracts,
        "failure_semantics": normalized_failure_semantics,
        "independent_validation": problem["independent_validation"],
        "figure_specs": problem["figure_specs"],
        "finite_domain": card["finite_domain"],
        "witness_strategy": card["witness_strategy"],
        "gap_or_tail_exclusion": card["gap_or_tail_exclusion"],
        "cost_model": card["cost_model"],
        "spike_spec": card["spike_spec"],
        "approximations": problem["approximations"],
    })


def _positive_number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ContractError(f"{field} must be finite numeric")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise ContractError(f"{field} must be {'nonnegative' if allow_zero else 'positive'}")
    return number


def _identified_specs(
    value: Any, field: str, *, allow_empty: bool = False
) -> list[dict[str, str]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ContractError(f"{field} must be {'a list' if allow_empty else 'a non-empty list'}")
    normalized = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict) or set(raw) != {"id", "description"}:
            raise ContractError(f"{field}[{index}] must have id and description")
        item_id = str(raw.get("id") or "")
        description = str(raw.get("description") or "").strip()
        if not re.fullmatch(r"^[a-z][a-z0-9_.-]{0,63}$", item_id) or item_id in seen:
            raise ContractError(f"{field}[{index}] has invalid or duplicate id")
        if not description:
            raise ContractError(f"{field}[{index}].description is empty")
        seen.add(item_id)
        normalized.append({"id": item_id, "description": description})
    return normalized


def validate_method_card(
    workspace: Path,
    inventory: dict[str, Any],
    problem_id: str,
    version: int,
) -> dict[str, Any]:
    directory = method_version_dir(workspace, problem_id, version)
    raw = _file_object(directory / "method_card.json", f"{problem_id} method card")
    expected = {
        "schema_version", "inventory_sha256", "proposal_version", "problem_id",
        "problem", "finite_domain", "witness_strategy", "gap_or_tail_exclusion",
        "cost_model", "spike_spec",
    }
    if set(raw) != expected or raw.get("schema_version") != 1:
        raise ContractError(f"{problem_id} method card keys/schema mismatch")
    if raw.get("problem_id") != problem_id or raw.get("proposal_version") != version:
        raise ContractError(f"{problem_id} method card identity/version mismatch")
    inventory_hash = canonical_hash(inventory)
    if raw.get("inventory_sha256") != inventory_hash:
        raise ContractError(f"{problem_id} method card inventory hash mismatch")
    inventory_problem = next(
        (item for item in inventory["problems"] if item["id"] == problem_id), None
    )
    if not inventory_problem:
        raise ContractError(f"{problem_id} is absent from accepted inventory")
    problem = raw.get("problem")
    if not isinstance(problem, dict):
        raise ContractError(f"{problem_id}.problem must be an object")
    if problem.get("id") != problem_id or problem.get("label") != inventory_problem["label"]:
        raise ContractError(f"{problem_id} problem identity differs from inventory")
    dependencies = _string_list(problem.get("depends_on", []), f"{problem_id}.depends_on", allow_empty=True)
    if dependencies != inventory_problem["depends_on"]:
        raise ContractError(f"{problem_id} dependencies differ from inventory")
    method = str(problem.get("method") or "").strip()
    if not method:
        raise ContractError(f"{problem_id}.method is empty")
    inputs = [relative_path(value) for value in _string_list(problem.get("inputs"), f"{problem_id}.inputs")]
    allowed_inputs = ("input/", "input_manifest.json", "reports/ANALYSIS_MODELING_REPORT.md")
    dependency_prefixes = tuple(
        prefix
        for dependency in dependencies
        for prefix in (f"code/{dependency}/", f"results/{dependency}/", f"reports/{dependency}_RESULTS.md")
    )
    for input_path_value in inputs:
        if not input_path_value.startswith(allowed_inputs + dependency_prefixes):
            raise ContractError(f"{problem_id} input is outside dependency boundary: {input_path_value}")
        if not (workspace / input_path_value).exists() and input_path_value != "reports/ANALYSIS_MODELING_REPORT.md":
            raise ContractError(f"{problem_id} input does not exist: {input_path_value}")
    outputs = [relative_path(value) for value in _string_list(problem.get("outputs"), f"{problem_id}.outputs")]
    allowed_outputs = (
        f"code/{problem_id}/", f"results/{problem_id}/", f"figures/{problem_id}/",
        f"reports/{problem_id}_RESULTS.md",
    )
    if any(not output.startswith(allowed_outputs) for output in outputs):
        raise ContractError(f"{problem_id} output is outside its artifact boundary")
    if not all(any(output.startswith(scope) for output in outputs) for scope in allowed_outputs[:2] + allowed_outputs[3:]):
        raise ContractError(f"{problem_id} must declare code, result, and report outputs")
    validation = _string_list(problem.get("validation"), f"{problem_id}.validation")
    runtime = problem.get("runtime_limit_seconds")
    if isinstance(runtime, bool) or not isinstance(runtime, int) or not 5 <= runtime <= 3600:
        raise ContractError(f"{problem_id}.runtime_limit_seconds must be 5..3600")
    requested_map = inventory_problem["requested_outputs"]
    requested_statements = [item["statement"] for item in requested_map]
    if problem.get("requested_outputs") != requested_statements:
        raise ContractError(f"{problem_id}.requested_outputs differ from inventory")
    required_output_ids = {item["id"] for item in requested_map}
    try:
        science = validate_problem_science(
            problem, problem_id, required_output_ids=required_output_ids
        )
        figures = validate_figure_specs({
            **problem,
            "inputs": inputs,
            "outputs": outputs,
            "claims": science["claims"],
        })
    except (ScientificContractError, ValueError) as exc:
        raise ContractError(str(exc)) from exc
    normalized_problem = {
        "id": problem_id,
        "label": inventory_problem["label"],
        "depends_on": dependencies,
        "method": method,
        "inputs": inputs,
        "outputs": outputs,
        "validation": validation,
        "runtime_limit_seconds": runtime,
        **science,
        "figure_specs": figures,
        "requested_output_map": requested_map,
    }
    text_fields = ("finite_domain", "witness_strategy", "gap_or_tail_exclusion")
    normalized = {
        "schema_version": 1,
        "inventory_sha256": inventory_hash,
        "proposal_version": version,
        "problem_id": problem_id,
        "problem": normalized_problem,
    }
    for field in text_fields:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"{problem_id}.{field} must be a non-empty string")
        normalized[field] = value.strip()
    cost = raw.get("cost_model")
    if not isinstance(cost, dict) or set(cost) != {
        "operation", "estimated_operations", "estimated_seconds", "memory_mb", "scaling"
    }:
        raise ContractError(f"{problem_id}.cost_model keys mismatch")
    normalized["cost_model"] = {
        "operation": str(cost.get("operation") or "").strip(),
        "estimated_operations": _positive_number(cost.get("estimated_operations"), "estimated_operations"),
        "estimated_seconds": _positive_number(cost.get("estimated_seconds"), "estimated_seconds"),
        "memory_mb": _positive_number(cost.get("memory_mb"), "memory_mb"),
        "scaling": str(cost.get("scaling") or "").strip(),
    }
    if not normalized["cost_model"]["operation"] or not normalized["cost_model"]["scaling"]:
        raise ContractError(f"{problem_id}.cost_model text fields are required")
    spike = raw.get("spike_spec")
    if not isinstance(spike, dict) or set(spike) != {
        "questions", "representative_cases", "required_metrics", "required_witnesses"
    }:
        raise ContractError(f"{problem_id}.spike_spec keys mismatch")
    normalized["spike_spec"] = {
        field: _identified_specs(
            spike.get(field),
            f"{problem_id}.spike_spec.{field}",
            allow_empty=field == "required_witnesses",
        )
        for field in ("questions", "representative_cases", "required_metrics", "required_witnesses")
    }
    all_spike_ids = [
        item["id"]
        for field in normalized["spike_spec"].values()
        for item in field
    ]
    if len(all_spike_ids) != len(set(all_spike_ids)):
        raise ContractError(f"{problem_id}.spike_spec IDs must be globally unique")
    normalized["method_spec_sha256"] = method_spec_hash(normalized)
    return normalized


def spike_budget(runtime_limit_seconds: int) -> int:
    return max(20, min(120, math.floor(runtime_limit_seconds * 0.10)))


def validate_spike_report(
    workspace: Path,
    card: dict[str, Any],
    *,
    supplemental: bool = False,
    source_version: int | None = None,
    supplemental_ids: set[str] | None = None,
) -> dict[str, Any]:
    directory = method_version_dir(
        workspace,
        card["problem_id"],
        source_version or card["proposal_version"],
    ) / "spike"
    if supplemental:
        directory /= "supplemental"
    path = directory / "spike_report.json"
    raw = _file_object(path, path.relative_to(workspace).as_posix())
    required = {
        "schema_version", "status", "problem_id", "method_spec_sha256",
        "budget_seconds", "actual_runtime_seconds", "answered_question_ids",
        "probe_scope", "benchmarks",
        "estimated_full_runtime_seconds", "peak_memory_mb", "witnesses",
        "unresolved_risks", "artifact_paths",
    }
    if set(raw) != required or raw.get("schema_version") != 1 or raw.get("status") != "candidate":
        raise ContractError("spike report keys/schema/status mismatch")
    if raw.get("problem_id") != card["problem_id"] or raw.get("method_spec_sha256") != card["method_spec_sha256"]:
        raise ContractError("spike report identity or method-spec hash mismatch")
    budget_value = raw.get("budget_seconds")
    expected_budget = 60 if supplemental else spike_budget(card["problem"]["runtime_limit_seconds"])
    if (
        isinstance(budget_value, bool)
        or not isinstance(budget_value, (int, float))
        or not math.isfinite(float(budget_value))
        or float(budget_value) != int(budget_value)
    ):
        raise ContractError("spike budget_seconds must be an integer-valued number")
    budget = int(budget_value)
    if budget < 1 or budget > expected_budget:
        raise ContractError(f"spike budget exceeds {expected_budget} seconds")
    actual = _positive_number(raw.get("actual_runtime_seconds"), "actual_runtime_seconds", allow_zero=True)
    if actual > budget:
        raise ContractError("spike actual runtime exceeds declared budget")
    question_ids = _string_list(
        raw.get("answered_question_ids"),
        "spike.answered_question_ids",
        allow_empty=supplemental,
    )
    scope = _string_list(
        raw.get("probe_scope"), "spike.probe_scope", allow_empty=supplemental
    )
    benchmarks_raw = raw.get("benchmarks")
    if not isinstance(benchmarks_raw, list) or (not benchmarks_raw and not supplemental):
        raise ContractError("spike benchmarks must be a list and primary benchmarks are non-empty")
    benchmarks = []
    for index, metric in enumerate(benchmarks_raw):
        if not isinstance(metric, dict) or set(metric) != {
            "metric_id", "name", "operations", "seconds", "throughput", "unit"
        }:
            raise ContractError(f"spike benchmark {index} keys mismatch")
        operations = _positive_number(metric.get("operations"), f"benchmark[{index}].operations")
        seconds = _positive_number(metric.get("seconds"), f"benchmark[{index}].seconds")
        throughput = _positive_number(metric.get("throughput"), f"benchmark[{index}].throughput")
        expected_throughput = operations / seconds
        if abs(throughput - expected_throughput) > max(1e-9, expected_throughput * 0.05):
            raise ContractError(f"spike benchmark {index} throughput is inconsistent")
        benchmarks.append({
            "metric_id": str(metric.get("metric_id") or ""),
            "name": str(metric.get("name") or "").strip(),
            "operations": operations,
            "seconds": seconds,
            "throughput": throughput,
            "unit": str(metric.get("unit") or "").strip(),
        })
        if not benchmarks[-1]["name"] or not benchmarks[-1]["unit"]:
            raise ContractError(f"spike benchmark {index} text fields are required")
    witnesses_raw = raw.get("witnesses")
    if not isinstance(witnesses_raw, list):
        raise ContractError("spike witnesses must be a list")
    witnesses = []
    for index, witness in enumerate(witnesses_raw):
        if not isinstance(witness, dict) or set(witness) != {
            "witness_id", "type", "summary", "artifact_paths"
        }:
            raise ContractError(f"spike witness {index} keys mismatch")
        witnesses.append({
            "witness_id": str(witness.get("witness_id") or ""),
            "type": str(witness.get("type") or "").strip(),
            "summary": str(witness.get("summary") or "").strip(),
            "artifact_paths": [relative_path(value) for value in _string_list(witness.get("artifact_paths"), f"witness[{index}].artifact_paths")],
        })
        if not witnesses[-1]["type"] or not witnesses[-1]["summary"]:
            raise ContractError(f"spike witness {index} text fields are required")
    full_expected = {
        "questions": {item["id"] for item in card["spike_spec"]["questions"]},
        "cases": {item["id"] for item in card["spike_spec"]["representative_cases"]},
        "metrics": {item["id"] for item in card["spike_spec"]["required_metrics"]},
        "witnesses": {item["id"] for item in card["spike_spec"]["required_witnesses"]},
    }
    all_expected = set().union(*full_expected.values())
    if supplemental:
        selected = supplemental_ids or set()
        if not selected or not selected <= all_expected:
            raise ContractError("supplemental Spike IDs are missing or invalid")
        expected_by_kind = {
            name: expected_ids & selected
            for name, expected_ids in full_expected.items()
        }
    else:
        expected_by_kind = full_expected
    expected_questions = expected_by_kind["questions"]
    expected_cases = expected_by_kind["cases"]
    expected_metrics = expected_by_kind["metrics"]
    expected_witnesses = expected_by_kind["witnesses"]
    coverage = {
        "questions": set(question_ids),
        "cases": set(scope),
        "metrics": {item["metric_id"] for item in benchmarks},
        "witnesses": {item["witness_id"] for item in witnesses},
    }
    if (
        len(question_ids) != len(coverage["questions"])
        or len(scope) != len(coverage["cases"])
        or len(benchmarks) != len(coverage["metrics"])
        or len(witnesses) != len(coverage["witnesses"])
    ):
        raise ContractError("spike coverage IDs must not be duplicated")
    for name, expected_ids in (
        ("questions", expected_questions),
        ("cases", expected_cases),
        ("metrics", expected_metrics),
        ("witnesses", expected_witnesses),
    ):
        if coverage[name] != expected_ids:
            raise ContractError(
                f"spike {name} coverage mismatch; missing={sorted(expected_ids - coverage[name])}, unknown={sorted(coverage[name] - expected_ids)}"
            )
    artifact_paths = [relative_path(value) for value in _string_list(raw.get("artifact_paths"), "spike.artifact_paths")]
    relative_directory = directory.relative_to(workspace).as_posix() + "/"
    for artifact in artifact_paths:
        if not artifact.startswith(relative_directory) or not (workspace / artifact).is_file():
            raise ContractError(f"spike artifact is missing or out of scope: {artifact}")
    probe_path = (directory / "probe.py").relative_to(workspace).as_posix()
    if not (directory / "probe.py").is_file() or probe_path not in artifact_paths:
        raise ContractError("spike probe.py is missing from declared artifacts")
    declared = set(artifact_paths)
    witness_artifacts = {
        path for witness in witnesses for path in witness["artifact_paths"]
    }
    if not witness_artifacts <= declared:
        raise ContractError("spike witness artifacts must be declared in artifact_paths")
    return {
        **raw,
        "budget_seconds": budget,
        "answered_question_ids": question_ids,
        "probe_scope": scope,
        "benchmarks": benchmarks,
        "actual_runtime_seconds": actual,
        "estimated_full_runtime_seconds": _positive_number(raw.get("estimated_full_runtime_seconds"), "estimated_full_runtime_seconds", allow_zero=True),
        "peak_memory_mb": _positive_number(raw.get("peak_memory_mb"), "peak_memory_mb", allow_zero=True),
        "witnesses": witnesses,
        "unresolved_risks": _string_list(raw.get("unresolved_risks"), "spike.unresolved_risks", allow_empty=True),
        "artifact_paths": artifact_paths,
    }


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ContractError(f"{field} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ContractError(f"{field} must contain non-empty strings")
    return [item.strip() for item in value]


def validate_execution_plan(workspace: Path) -> dict[str, Any]:
    """Validate and normalize the model-generated execution plan."""
    path = workspace / "execution_plan.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError("missing execution_plan.json") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"execution_plan.json is invalid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") not in {1, 2}:
        raise ContractError("execution_plan.json schema_version must be 1 or 2")
    schema_version = int(raw["schema_version"])
    if schema_version == 2 and (
        isinstance(raw.get("plan_version"), bool)
        or not isinstance(raw.get("plan_version"), int)
        or raw["plan_version"] < 1
    ):
        raise ContractError("schema v2 execution plan requires positive plan_version")
    problems = raw.get("problems")
    if not isinstance(problems, list) or not problems:
        raise ContractError("execution_plan.json problems must be a non-empty list")
    if len(problems) > 20:
        raise ContractError("execution_plan.json has more than 20 problems")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    all_claim_ids: set[str] = set()
    all_approximation_ids: set[str] = set()
    for index, item in enumerate(problems):
        if not isinstance(item, dict):
            raise ContractError(f"problems[{index}] must be an object")
        problem_id = str(item.get("id") or "")
        if not PROBLEM_ID_RE.fullmatch(problem_id):
            raise ContractError(f"invalid problem id: {problem_id!r}")
        if problem_id in seen:
            raise ContractError(f"duplicate problem id: {problem_id}")
        label = str(item.get("label") or "").strip()
        method = str(item.get("method") or "").strip()
        if not label or not method:
            raise ContractError(f"{problem_id} requires label and method")
        dependencies = _string_list(
            item.get("depends_on", []), f"{problem_id}.depends_on", allow_empty=True
        )
        unknown = [dependency for dependency in dependencies if dependency not in seen]
        if unknown:
            raise ContractError(
                f"{problem_id} depends on future or unknown problems: {', '.join(unknown)}"
            )
        if len(set(dependencies)) != len(dependencies):
            raise ContractError(f"{problem_id}.depends_on contains duplicates")

        inputs = [relative_path(value) for value in _string_list(item.get("inputs"), f"{problem_id}.inputs")]
        allowed_inputs = ("input/", "input_manifest.json", "reports/ANALYSIS_MODELING_REPORT.md")
        dependency_prefixes = tuple(
            prefix
            for dependency in dependencies
            for prefix in (
                f"code/{dependency}/",
                f"results/{dependency}/",
                f"reports/{dependency}_RESULTS.md",
            )
        )
        for input_path in inputs:
            if not input_path.startswith(allowed_inputs + dependency_prefixes):
                raise ContractError(f"{problem_id} input is outside its dependency boundary: {input_path}")
            if input_path.startswith("input/") and not (workspace / input_path).exists():
                raise ContractError(f"{problem_id} input does not exist: {input_path}")

        outputs = [relative_path(value) for value in _string_list(item.get("outputs"), f"{problem_id}.outputs")]
        allowed_outputs = (
            f"code/{problem_id}/",
            f"results/{problem_id}/",
            f"figures/{problem_id}/",
            f"reports/{problem_id}_RESULTS.md",
        )
        for output in outputs:
            if not output.startswith(allowed_outputs):
                raise ContractError(f"{problem_id} output is outside its artifact boundary: {output}")
        required_output_scopes = (
            f"code/{problem_id}/",
            f"results/{problem_id}/",
            f"reports/{problem_id}_RESULTS.md",
        )
        if not all(any(output.startswith(scope) for output in outputs) for scope in required_output_scopes):
            raise ContractError(f"{problem_id} must declare code, result, and report outputs")

        validation = _string_list(item.get("validation"), f"{problem_id}.validation")
        runtime = item.get("runtime_limit_seconds")
        if isinstance(runtime, bool) or not isinstance(runtime, int) or not 5 <= runtime <= 3600:
            raise ContractError(f"{problem_id}.runtime_limit_seconds must be 5..3600")
        normalized_problem = {
            "id": problem_id,
            "label": label,
            "depends_on": dependencies,
            "method": method,
            "inputs": inputs,
            "outputs": outputs,
            "validation": validation,
            "runtime_limit_seconds": runtime,
        }
        if schema_version == 2:
            requested_map = item.get("requested_output_map")
            required_output_ids: set[str] | None = None
            normalized_requested_map = None
            if requested_map is not None:
                if not isinstance(requested_map, list) or not requested_map:
                    raise ContractError(f"{problem_id}.requested_output_map must be non-empty")
                normalized_requested_map = []
                required_output_ids = set()
                for output_index, raw_output in enumerate(requested_map):
                    if not isinstance(raw_output, dict) or set(raw_output) != {"id", "statement"}:
                        raise ContractError(f"{problem_id}.requested_output_map[{output_index}] is invalid")
                    output_id = str(raw_output.get("id") or "")
                    statement = str(raw_output.get("statement") or "").strip()
                    if not re.fullmatch(r"^[a-z][a-z0-9_.-]{0,63}$", output_id) or output_id in required_output_ids:
                        raise ContractError(f"invalid or duplicate requested output id: {output_id!r}")
                    required_output_ids.add(output_id)
                    normalized_requested_map.append({"id": output_id, "statement": statement})
                if [entry["statement"] for entry in normalized_requested_map] != item.get("requested_outputs"):
                    raise ContractError(f"{problem_id}.requested_output_map differs from requested_outputs")
            try:
                science = validate_problem_science(
                    item, problem_id, required_output_ids=required_output_ids
                )
            except ScientificContractError as exc:
                raise ContractError(str(exc)) from exc
            claim_ids = {claim["id"] for claim in science["claims"]}
            approximation_ids = {
                approximation["id"] for approximation in science["approximations"]
            }
            if claim_ids & all_claim_ids:
                raise ContractError("claim ids must be globally unique")
            if approximation_ids & all_approximation_ids:
                raise ContractError("approximation ids must be globally unique")
            all_claim_ids.update(claim_ids)
            all_approximation_ids.update(approximation_ids)
            normalized_problem.update(science)
            if normalized_requested_map is not None:
                normalized_problem["requested_output_map"] = normalized_requested_map
            try:
                normalized_problem["figure_specs"] = validate_figure_specs({
                    **item,
                    "id": problem_id,
                    "inputs": inputs,
                    "outputs": outputs,
                    "claims": science["claims"],
                })
            except ValueError as exc:
                raise ContractError(str(exc)) from exc
        normalized.append(normalized_problem)
        seen.add(problem_id)
    result = {"schema_version": schema_version, "problems": normalized}
    if schema_version == 2:
        result["plan_version"] = int(raw["plan_version"])
    return result


def initial_workflow(
    planner: dict[str, str], worker: dict[str, str], *, contract_version: int = 1
) -> dict[str, Any]:
    first_id = "inventory" if contract_version == 3 else "planning"
    phases = [
        {
            "id": first_id,
            "label": "问题清单" if contract_version == 3 else "赛题分析与建模",
            "status": "running",
            "attempts": 1,
            "started_at": None,
            "completed_at": None,
            "last_error": "",
        }
    ]
    if contract_version in {2, 3}:
        phases.append(
            {
                "id": "inventory_audit" if contract_version == 3 else "plan_audit",
                "label": "问题清单审查" if contract_version == 3 else "独立计划审查",
                "status": "pending",
                "attempts": 0,
                "started_at": None,
                "completed_at": None,
                "last_error": "",
            }
        )
    return {
        "schema_version": 1,
        "contract_version": contract_version,
        "plan_version": 0 if contract_version == 3 else 1,
        "current": first_id,
        "mode": first_id if contract_version == 3 else "run",
        "profiles": {"planner": planner, "worker": worker},
        "phases": phases,
        "frozen": {},
        **({"inventory_version": 1} if contract_version == 3 else {}),
    }


def expand_problem_phases(workflow: dict[str, Any], plan: dict[str, Any]) -> None:
    contract_version = workflow.get("contract_version")
    prefix_count = 2 if contract_version in {2, 3} else 1
    prefix = workflow["phases"][:prefix_count]
    if contract_version == 3:
        problem_phases = []
        for problem in plan["problems"]:
            problem_id = problem["id"]
            for kind, label in (
                ("method", f"{problem['label']}：方法设计"),
                ("spike", f"{problem['label']}：可行性探针"),
                ("method_audit", f"{problem['label']}：方法审查"),
                ("problem", problem["label"]),
            ):
                problem_phases.append({
                    "id": f"{kind}:{problem_id}",
                    "problem_id": problem_id,
                    "label": label,
                    "status": "pending",
                    "attempts": 0,
                    "started_at": None,
                    "completed_at": None,
                    "last_error": "",
                })
        final_phases = V3_FINAL_PHASES
    else:
        problem_phases = [
            {
                "id": f"problem:{problem['id']}",
                "problem_id": problem["id"],
                "label": problem["label"],
                "status": "pending",
                "attempts": 0,
                "started_at": None,
                "completed_at": None,
                "last_error": "",
            }
            for problem in plan["problems"]
        ]
        final_phases = V2_FINAL_PHASES if contract_version == 2 else FINAL_PHASES
    workflow["phases"] = prefix + problem_phases + [
        {
            "id": phase_id,
            "label": label,
            "status": "pending",
            "attempts": 0,
            "started_at": None,
            "completed_at": None,
            "last_error": "",
        }
        for phase_id, label in final_phases
    ]


def result_errors(workspace: Path, problem: dict[str, Any]) -> list[str]:
    problem_id = problem["id"]
    errors: list[str] = []
    code_dir = workspace / "code" / problem_id
    result_dir = workspace / "results" / problem_id
    report = workspace / "reports" / f"{problem_id}_RESULTS.md"
    if not code_dir.is_dir() or not any(path.is_file() for path in code_dir.rglob("*")):
        errors.append(f"artifact_missing: code/{problem_id}/ has no implementation")
    if not report.is_file() or not report.stat().st_size:
        errors.append(f"artifact_missing: reports/{problem_id}_RESULTS.md")

    if "claims" in problem:
        errors.extend(candidate_errors(workspace, problem))
        for output in problem["outputs"]:
            if not (workspace / output).exists():
                errors.append(f"artifact_missing: {output}")
        return errors

    result = _read_json(result_dir / "result.json", errors, "result.json")
    if result is not None:
        if result.get("problem_id") != problem_id or result.get("status") != "pass":
            errors.append("validation_failed: result.json identity/status")
        metrics = result.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            errors.append("validation_failed: result.json metrics")
        else:
            for index, metric in enumerate(metrics):
                valid = (
                    isinstance(metric, dict)
                    and isinstance(metric.get("name"), str)
                    and isinstance(metric.get("value"), (int, float))
                    and not isinstance(metric.get("value"), bool)
                    and math.isfinite(float(metric.get("value")))
                    and isinstance(metric.get("unit"), str)
                    and isinstance(metric.get("description"), str)
                )
                if not valid:
                    errors.append(f"validation_failed: result.json metrics[{index}]")

    verification = _read_json(result_dir / "verification.json", errors, "verification.json")
    if verification is not None:
        if verification.get("status") != "pass":
            errors.append("validation_failed: verification.json status")
        for field in ("smoke_runtime_seconds", "estimated_runtime_seconds", "actual_runtime_seconds"):
            value = verification.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                errors.append(f"validation_failed: verification.json {field}")
        checks = verification.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append("validation_failed: verification.json checks")

    for output in problem["outputs"]:
        if not (workspace / output).exists():
            errors.append(f"artifact_missing: {output}")
    return errors


def _read_json(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"artifact_missing: {path.as_posix()}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"validation_failed: {label} invalid JSON ({exc.msg})")
        return None
    if not isinstance(value, dict):
        errors.append(f"validation_failed: {label} must be an object")
        return None
    return value


def _transient_python_artifact(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}


def artifact_hashes(workspace: Path, problem_id: str) -> dict[str, str]:
    roots = (
        workspace / "code" / problem_id,
        workspace / "results" / problem_id,
        workspace / "figures" / problem_id,
        workspace / "reports" / f"{problem_id}_RESULTS.md",
        workspace / "reports" / f"{problem_id}_SCIENTIFIC_REVIEW.json",
    )
    hashes: dict[str, str] = {}
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.is_dir() else []
        for path in paths:
            if path.is_file() and not _transient_python_artifact(path):
                relative = path.relative_to(workspace).as_posix()
                hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def frozen_errors(workspace: Path, frozen: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for problem_id, expected in frozen.items():
        if not isinstance(expected, dict):
            errors.append(f"artifact_changed: invalid frozen record for {problem_id}")
            continue
        actual = artifact_hashes(workspace, problem_id)
        if actual != expected:
            errors.append(f"artifact_changed: frozen artifacts for {problem_id}")
    return errors


def workspace_hashes(workspace: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or _transient_python_artifact(path):
            continue
        relative = path.relative_to(workspace)
        if relative.parts[0].startswith(".pi") or relative.as_posix() == "project.json":
            continue
        hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def stage_scope_errors(
    workspace: Path,
    baseline: dict[str, str],
    stage: str,
    *,
    planning_version: int = 1,
    supplemental_spike: bool = False,
) -> list[str]:
    """Reject writes outside the current stage's artifact boundary."""
    if not baseline:
        return []
    current = workspace_hashes(workspace)
    changed = {
        path
        for path in set(baseline) | set(current)
        if baseline.get(path) != current.get(path)
    }
    if stage == "inventory":
        allowed = (
            f"planning/inventory/v{planning_version}/",
            f"reports/PROBLEM_INVENTORY_v{planning_version}.md",
        )
    elif stage == "inventory_audit" or stage.startswith("method_audit:"):
        allowed = ()
    elif stage.startswith("method:"):
        problem_id = stage.split(":", 1)[1]
        allowed = (
            f"planning/methods/{problem_id}/v{planning_version}/method_card.json",
            f"reports/{problem_id}_METHOD_v{planning_version}.md",
        )
    elif stage.startswith("spike:"):
        problem_id = stage.split(":", 1)[1]
        suffix = "/supplemental/" if supplemental_spike else "/"
        allowed = (
            f"planning/methods/{problem_id}/v{planning_version}/spike{suffix}",
        )
    elif stage == "planning":
        allowed = ("execution_plan.json", "reports/ANALYSIS_MODELING_REPORT.md")
    elif stage.startswith("problem:"):
        problem_id = stage.split(":", 1)[1]
        allowed = (
            f"code/{problem_id}/",
            f"results/{problem_id}/",
            f"figures/{problem_id}/",
            f"reports/{problem_id}_RESULTS.md",
            f"reports/{problem_id}_REPAIR_REVIEW.md",
            f"reports/{problem_id}_SCIENTIFIC_REVIEW.json",
            "execution_plan.revision.json",
        )
    elif stage == "plan_audit":
        allowed = ("reports/PLAN_AUDIT.json", "reports/PLAN_AUDIT.md")
    elif stage == "paper_planning":
        allowed = ("paper_plan.json", "reports/PAPER_PLAN.md")
    elif stage == "diagram":
        allowed = ("figures/", "reports/DRAWIO_REPORT.md")
    elif stage == "writing":
        allowed = ("paper/",)
    elif stage == "verify":
        allowed = ("reports/VERIFY_REPORT.md", "_tmp/")
    else:
        return [f"artifact_changed: unknown stage boundary {stage}"]
    disallowed = sorted(
        path
        for path in changed
        if not any(
            path.startswith(prefix) if prefix.endswith("/") else path == prefix
            for prefix in allowed
        )
    )
    return [f"artifact_changed: stage wrote outside its boundary: {path}" for path in disallowed]


def inventory_prompt(
    *,
    problem_file: str,
    version: int,
    competition: str,
    language: str,
    notes: str,
    evidence_paths: list[str] | None = None,
) -> str:
    scope = _evidence_scope(evidence_paths)
    return f"""You are the problem-inventory maker. Use this Host-owned evidence scope:
{scope}

Read $MATHMODELAGENT_ROOT/skills/2analysis-modeling/SKILL.md and $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md. Do not design algorithms, run computations, write execution_plan.json, or begin a solution.

Write only planning/inventory/v{version}/problem_inventory.json and reports/PROBLEM_INVENTORY_v{version}.md. The JSON has exactly schema_version=1 and an ordered non-empty problems array. Each problem has exactly id, label, depends_on, requested_outputs, input_paths, interpretation, ambiguities, and suggested_evidence_level. requested_outputs is a non-empty list of {{id,statement}} copied from the task; use globally unique stable IDs. Dependencies must reference earlier problem IDs. input_paths may contain only existing input/ paths or input_manifest.json. ambiguities is a list of {{id,interpretations,impact,resolution_needed}}. suggested_evidence_level is A_certified, B_bounded_numerical, or C_exploratory and is advisory only. Do not hide a requested output inside interpretation or add speculative outputs.

Competition: {competition}. Paper language: {language}. User notes: {notes or 'None'}. Stop after both inventory artifacts exist."""


def local_artifact_repair_prompt(
    base_prompt: str,
    *,
    artifact: str,
    version: int,
    errors: list[str],
) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"""{base_prompt}

The Host rejected the current {artifact} artifacts. This is a same-version local format repair for version {version}, not a semantic revision. Re-read and repair only the current stage artifacts in place. Do not create another version, change accepted upstream evidence, start a Spike or execution, or search Host validators/tests. Preserve scientifically valid content and correct every gate error below.

Gate evidence:
{details}"""


def inventory_audit_prompt(
    inventory: dict[str, Any], evidence_paths: list[str] | None = None
) -> str:
    contract = _review_json_contract("inventory", "")
    payload = json.dumps(inventory, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    return f"""Act as the read-only independent problem-inventory auditor. Use this Host-owned evidence scope:
{scope}

Re-read $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md plus the original problem and inputs, then check exact problem decomposition, requested-output coverage, dependency order, input boundaries, interpretations, and surfaced ambiguities. Do not design methods, edit/create files, or run computation.

Inventory:
{payload}

Return only strict JSON with null problem_id matching this shape. Treat implementation_fidelity as inventory fidelity:
{contract}"""


def inventory_revision_prompt(
    review: dict[str, Any],
    version: int,
    evidence_paths: list[str] | None = None,
) -> str:
    payload = json.dumps(review, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    return f"""Revise only the rejected problem inventory using this Host-owned evidence scope:
{scope}

Write a new append-only version at planning/inventory/v{version}/problem_inventory.json and reports/PROBLEM_INVENTORY_v{version}.md. Do not alter prior versions, design methods, write execution_plan.json, or run computation. Resolve every audit issue while preserving exact task outputs and honest ambiguities.

Audit:
{payload}"""


def method_proposal_prompt(
    inventory: dict[str, Any],
    problem_id: str,
    version: int,
    inventory_sha256: str,
    evidence_paths: list[str] | None = None,
) -> str:
    inventory_problem = next(item for item in inventory["problems"] if item["id"] == problem_id)
    payload = json.dumps(inventory_problem, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    problem_shape = """{
  "id": "problem id",
  "label": "exact inventory label",
  "depends_on": [],
  "method": "one non-empty executable-method description",
  "inputs": ["existing input/... paths, input_manifest.json, and declared accepted dependency paths only"],
  "outputs": ["code/<id>/solve.py", "results/<id>/result.json", "reports/<id>_RESULTS.md"],
  "validation": ["non-empty validation descriptions"],
  "runtime_limit_seconds": 120,
  "requested_outputs": ["exact inventory statements only"],
  "interpretation": "one non-empty string",
  "assumptions": [{"id":"...","statement":"...","rationale":"...","validation":"..."}],
  "claims": [{"id":"...","type":"optimality","statement":"...","evidence_required":["..."],"acceptance":{"criterion":"...","tolerance":1e-9},"evidence_level":"A_certified","requested_output_ids":["..."],"limitations":[]}],
  "approximations": [{"id":"...","original_quantity":"...","surrogate_quantity":"...","justification":"...","error_or_equivalence_check":"..."}],
  "failure_semantics": [{"id":"...","event_id":"...","category":"numerical_failure","condition":"...","action":"..."}],
  "independent_validation": [{"id":"...","method":"...","independent_from":"...","claims":["..."]}],
  "figure_specs": []
}"""
    return f"""Design one method card for {problem_id} only. Use this Host-owned evidence scope:
{scope}

Read $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md and $MATHMODELAGENT_ROOT/pi/skills/mathmodel-figure-quality/references/figure-reference-catalog.json, then use this accepted inventory entry:
{payload}

Write only planning/methods/{problem_id}/v{version}/method_card.json and reports/{problem_id}_METHOD_v{version}.md. Do not write execution_plan.json, Spike code, formal result artifacts, or later methods. The JSON must have exactly schema_version=1, inventory_sha256='{inventory_sha256}', proposal_version={version}, problem_id='{problem_id}', problem, finite_domain, witness_strategy, gap_or_tail_exclusion, cost_model, and spike_spec.

`problem` must have exactly these top-level fields and no custom `schema_version`, `input_paths`, `model`, `deliverables`, or nested replacement objects:
```json
{problem_shape}
```
All `inputs` and `outputs` entries are literal relative path strings, never descriptions such as "candidate metrics". `outputs` must include at least one path under each of `code/{problem_id}/`, `results/{problem_id}/`, and the exact file `reports/{problem_id}_RESULTS.md`; figure artifacts use `figures/{problem_id}/`. Every future generated artifact named by the method must be declared as a path in `outputs`.

Claim `type` is exactly one of `identity_or_constraint`, `feasibility`, `optimality`, `event_or_boundary`, `approximation`, `estimate_or_prediction`, `ranking_or_decision`, or `other`. Every claim adds evidence_level, requested_output_ids, and limitations (non-empty for Level B/C). Every requested-output ID needs at least one A_certified or B_bounded_numerical claim; C_exploratory is supplementary only. The union of all `independent_validation[].claims` must equal the complete set of claim IDs; every claim requires independent validation, including figure, limitation, implementation-constraint, and paper-readiness claims. Every failure_semantics object has exactly id, event_id, category, condition, and action; category is `domain_event`, `mathematical_infeasibility`, `numerical_failure`, `data_or_input_failure`, or `decision_outcome`. Conditions describing the same physical event share event_id and category.

If figures are required, every `figure_specs` object has exactly `id`, `claim_ids`, `purpose`, `plot_family`, `reference_id`, `panels`, `primary_encoding`, `secondary_encoding`, `required_annotations`, `final_width`, `vector_path`, `preview_path`, `generator_path`, `data_paths`, and `required_data_fields`. `panels` contains 1--3 non-empty distinct strings. `primary_encoding` is exactly `position`, `length`, `color`, `area`, or `angle`; `final_width` is exactly `single_column`, `double_column`, or `full`; `secondary_encoding` is one non-empty descriptive string. `generator_path` must be under `code/{problem_id}/`; vector/preview paths must be under `figures/{problem_id}/`; generated data paths must be under `results/{problem_id}/`; every generated path must also appear in `outputs`. Copy plot_family exactly from the selected catalog reference.

The top-level `finite_domain`, `witness_strategy`, and `gap_or_tail_exclusion` values are each one non-empty string, not objects or arrays. State finite domains, witnesses/brackets, gap/tail exclusion, mutually exclusive failure semantics, independent validation, figure specs, and a realistic runtime limit. cost_model has exactly operation, estimated_operations, estimated_seconds, memory_mb, scaling. spike_spec has exactly questions, representative_cases, required_metrics and required_witnesses; each list entry is {{id,description}}, IDs are globally unique, and only required_witnesses may be empty. Prefer the cheapest scientifically honest evidence; never invent an unaffordable formal certificate. Stop after the two method artifacts."""


def method_revision_prompt(
    inventory: dict[str, Any],
    problem_id: str,
    version: int,
    review: dict[str, Any],
    evidence_paths: list[str] | None = None,
) -> str:
    base = method_proposal_prompt(
        inventory, problem_id, version, canonical_hash(inventory), evidence_paths
    )
    evidence = json.dumps(review, ensure_ascii=False, indent=2)
    return f"""{base}

This is a targeted revision. Read the immediately preceding immutable proposal version under planning/methods/{problem_id}/ before writing v{version}. Resolve every item below without changing the accepted inventory or upstream artifacts. Do not strengthen evidence beyond what is applicable and affordable.

Method audit evidence:
{evidence}"""


def evidence_downgrade_prompt(
    inventory: dict[str, Any],
    problem_id: str,
    version: int,
    review: dict[str, Any],
    evidence_paths: list[str] | None = None,
) -> str:
    base = method_revision_prompt(
        inventory, problem_id, version, review, evidence_paths
    )
    return f"""{base}

This final revision is downgrade-only. Change only Reviewer-listed A_certified claims to B_bounded_numerical and update their statement, evidence, acceptance criterion, uncertainty, and limitations honestly. Requested-output scope, method-spec fields, dependencies, outputs, validation, failure semantics, independent validation, and figures may not change. Do not introduce C_exploratory coverage."""


def spike_prompt(
    card: dict[str, Any],
    *,
    supplemental: bool = False,
    supplemental_ids: list[str] | None = None,
    evidence_paths: list[str] | None = None,
) -> str:
    problem_id = card["problem_id"]
    version = card["proposal_version"]
    suffix = "/supplemental" if supplemental else ""
    budget = 60 if supplemental else spike_budget(card["problem"]["runtime_limit_seconds"])
    payload = json.dumps(card, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    target_note = (
        f" For this supplemental Spike, cover exactly these planned IDs: {supplemental_ids}."
        if supplemental
        else " Cover every planned Spike ID exactly once."
    )
    return f"""Run one {'supplemental ' if supplemental else ''}feasibility Spike for {problem_id}. This is planning evidence, not a formal solution. Use this Host-owned evidence scope:
{scope}

Use this accepted method card:
{payload}

Write only planning/methods/{problem_id}/v{version}/spike{suffix}/. Put executable probe code in probe.py and strict data in spike_report.json. The total declared runtime budget is at most {budget} seconds. Do not write code/, results/, figures/, reports/, execution_plan.json, or accepted artifacts. Benchmark representative kernel operations and produce required witnesses/brackets when requested; do not run the full solution.

spike_report.json has exactly schema_version=1, status='candidate', problem_id, method_spec_sha256='{card['method_spec_sha256']}', budget_seconds, actual_runtime_seconds, answered_question_ids, probe_scope, benchmarks, estimated_full_runtime_seconds, peak_memory_mb, witnesses, unresolved_risks, and artifact_paths. answered_question_ids and probe_scope contain planned question/case IDs. Each benchmark adds metric_id to name, operations, seconds, throughput, unit; each witness adds witness_id to type, summary, artifact_paths.{target_note} Every artifact, including probe.py and witness files, must be listed and stay inside this Spike directory. Timeout or process failure is numerical/planning feasibility evidence, never mathematical infeasibility or a domain event. Stop after writing the candidate report."""


def spike_repair_prompt(
    card: dict[str, Any],
    errors: list[str],
    *,
    supplemental: bool = False,
    supplemental_ids: list[str] | None = None,
) -> str:
    problem_id = card["problem_id"]
    version = card["proposal_version"]
    suffix = "/supplemental" if supplemental else ""
    details = "\n".join(f"- {error}" for error in errors)
    target_note = (
        f"Cover exactly these planned IDs: {supplemental_ids}."
        if supplemental
        else "Cover every planned Spike ID exactly once."
    )
    return f"""The Host rejected the current Spike artifacts for {problem_id}. Repair only planning/methods/{problem_id}/v{version}/spike{suffix}/ under the same immutable Method Card; do not create a new Method Card or modify input, accepted dependencies, code, results, figures, reports, or execution_plan.json.

Gate evidence:
{details}

Re-read the current probe.py, spike_report.json, witness files, and planning/methods/{problem_id}/v{version}/method_card.json. Correct the rejected schema, coverage, artifact declaration, or bounded probe evidence without changing measured values dishonestly. Reuse valid existing computation and rerun only the smallest missing check within the remaining budget. {target_note} A timeout or failed probe remains numerical/planning feasibility evidence, never mathematical infeasibility. Stop after the repaired Spike artifacts exist."""


def method_audit_prompt(
    inventory_problem: dict[str, Any],
    card: dict[str, Any],
    spike: dict[str, Any],
    evidence_paths: list[str] | None = None,
) -> str:
    contract = json.dumps({
        "schema_version": 1,
        "review_type": "method",
        "problem_id": card["problem_id"],
        "verdict": "accept | revise | blocked",
        "statement_alignment": "pass | fail",
        "method_validity": "pass | fail",
        "computational_feasibility": "pass | fail",
        "evidence_calibration": "pass | fail",
        "validation_independence": "pass | fail",
        "dependency_consistency": "pass | fail",
        "figure_contract": "pass | fail",
        "issue_class": "none | method | ambiguity | evidence | budget | blocked",
        "issues": [],
        "required_repairs": [],
        "supplemental_spike": False,
        "supplemental_spike_ids": [],
        "allowed_downgrades": [],
    }, ensure_ascii=False, indent=2)
    payload = json.dumps({
        "inventory": inventory_problem,
        "method_card": card,
        "spike_report": spike,
    }, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    return f"""Act as the independent read-only Method Auditor. Use this Host-owned evidence scope:
{scope}

Read $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md and the package below. Do not edit/create files or run expensive computation.

Check statement alignment, mathematical applicability, real Spike-based computational feasibility, declared evidence level, independent validation, dependency consistency, and figure semantics. Prefer the cheapest scientifically honest evidence. Do not demand formal proof for a bounded numerical modeling answer; reject proof inflation, unbounded searches, missing event witnesses, conflicting failure meanings, unaffordable operation counts, or validations equivalent to the primary method. A timeout is not mathematical infeasibility. Request supplemental_spike only for one specific unresolved measurement and list exactly its existing planned IDs in supplemental_spike_ids; otherwise use false and []. allowed_downgrades may contain only {{claim_id,from:'A_certified',to:'B_bounded_numerical',reason}}.

Package:
{payload}

Return only strict JSON matching:
{contract}"""


def planning_prompt(*, problem_file: str, competition: str, language: str, paper_engine: str, notes: str) -> str:
    return f"""You are the planning stage only. Fully read $MATHMODELAGENT_ROOT/skills/2analysis-modeling/SKILL.md, $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md, {problem_file}, input_manifest.json, and relevant files under input/.

Use competition family {competition}, paper language {language}, and paper engine {paper_engine}. Write reports/ANALYSIS_MODELING_REPORT.md and execution_plan.json. Do not write solver code, run the full solution, draw diagrams, or write the paper. Stop after both planning artifacts exist.

execution_plan.json must use schema_version 2 and plan_version 1 with an ordered non-empty problems array. Each problem requires the existing id, label, depends_on, method, inputs, outputs, validation, and runtime_limit_seconds fields plus: requested_outputs, interpretation, assumptions, claims, approximations, failure_semantics, independent_validation, and figure_specs.

Every claim needs a stable id, a generic claim type, its exact statement, required evidence, and an acceptance criterion with an optional numeric tolerance. Use these exact nested shapes: assumptions=[{{id,statement,rationale,validation}}]; claims=[{{id,type,statement,evidence_required,acceptance:{{criterion,tolerance}}}}]; approximations=[{{id,original_quantity,surrogate_quantity,justification,error_or_equivalence_check}}]; failure_semantics=[{{condition,classification,action}}]; independent_validation=[{{id,method,independent_from,claims}}]. Every claim must be covered by an independent_validation entry that states how it differs from the primary method. assumptions and approximations may be empty arrays, but failure_semantics, claims, and independent_validation may not. Any surrogate, discretization, dimensional reduction, heuristic, or proxy must be declared with a justification and an error/equivalence check. Every failure condition must have one unambiguous classification; a domain event that makes a candidate infeasible is not a numerical failure or structural mathematical infeasibility.

For every first-event, continuous-path, global-extremum, feasibility-boundary, or minimum/maximum claim, state a finite search domain, how a feasible/event witness is obtained, how the unsearched prefix/tail or gaps are excluded, what conservative bound or convergence test is computable, and the actual worst-case operation count. Do not propose formal interval/Krawczyk/branch-and-bound certification unless its square system or bounding operator is explicitly defined and the cost fits the runtime limit. When a formal global proof is not affordable, make a truthful numerical-estimate claim with declared domain, resolution, uncertainty and independent adversarial validation; never upgrade numerical evidence to a theorem by wording.

Inputs may use input/, input_manifest.json, reports/ANALYSIS_MODELING_REPORT.md, and declared earlier dependencies only. Outputs must stay under code/<id>/, results/<id>/, figures/<id>/, or reports/<id>_RESULTS.md. Every problem must declare code, result, and report outputs.

Read $MATHMODELAGENT_ROOT/pi/skills/mathmodel-figure-quality/references/figure-reference-catalog.json before planning figures. `figure_specs` is `[]` when no scientific figure is warranted. Otherwise each entry must use exactly: id, claim_ids, purpose, plot_family, reference_id, panels, primary_encoding, secondary_encoding, required_annotations, final_width, vector_path, preview_path, generator_path, data_paths, and required_data_fields. Copy plot_family exactly from the selected catalog reference. Use 1--3 panels and final_width `single_column`, `double_column`, or `full`; primary_encoding is one of `position`, `length`, `color`, `area`, or `angle`. Declare every vector master, PNG preview, generating script, and generated data path in outputs. `required_data_fields` must name the real CSV headers or JSON keys needed to support every plotted bound, bracket, uncertainty, threshold, certificate link and annotation. Each figure must state which claim, reader takeaway, and decision it supports. Use `event-01-threshold-bracket`, not a sensitivity reference, for first-event evidence. Do not require a figure merely for decoration or page count. Reference previews are layout inspiration only and are never evidence. State correctness conditions, complexity, stopping rule, and expected runtime before choosing a method.

User notes: {notes or 'None'}"""


def problem_prompt(
    problem: dict[str, Any],
    evidence_paths: list[str] | None = None,
    figure_references: list[dict[str, Any]] | None = None,
) -> str:
    payload = json.dumps(problem, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    reference_payload = json.dumps(figure_references or [], ensure_ascii=False, indent=2)
    return f"""Execute exactly one modeling subproblem and then stop. Read $MATHMODELAGENT_ROOT/skills/3coding-visual/SKILL.md, $MATHMODELAGENT_ROOT/pi/skills/mathmodel-figure-quality/SKILL.md, and its references/figure-routing.md, then use this Host-owned evidence scope:
{scope}

Use only these Host-selected figure-reference catalog entries and their exact `$MATHMODELAGENT_ROOT/<preview_path>` previews; do not search the full catalog:
{reference_payload}

Task contract:

{payload}

Do not start any later problem. Write implementation only under code/{problem['id']}/, numerical artifacts under results/{problem['id']}/, data figures under figures/{problem['id']}/, and the report reports/{problem['id']}_RESULTS.md. Treat input/ and every earlier problem directory as read-only.

First implement and time the smallest representative computation. Record smoke_runtime_seconds and estimated_runtime_seconds before the full run. If the estimate exceeds {problem['runtime_limit_seconds']} seconds, change the algorithm before running it. Use one fixed mathematical predicate for search and acceptance; extra precision checks may test sensitivity but may not redefine feasibility. Keep numerical errors distinct from mathematical infeasibility. Implement every declared claim and independent validation; do not silently introduce an approximation absent from the contract.

For every scientific data figure, follow its Planner-owned `figure_specs` entry. Open only the Host-selected reference preview above and preserve its explanatory structure, hierarchy and encoding logic. Do not substitute another reference or change the planned claims, purpose, family, paths or panels. Ordinary plots must use SciencePlots + the official Seaborn/Matplotlib API; specialized template layouts are allowed only when selected by the plan. Replace all reference/template simulation with current workspace data. Save the generator, source data, vector master and PNG preview at the exact planned paths. Render and inspect at final paper size.

Write results/{problem['id']}/result.json with problem_id, status='candidate', and non-empty metrics entries containing name, finite numeric value, unit, and description. Write results/{problem['id']}/verification.json with schema_version=2, status='candidate', smoke_runtime_seconds, estimated_runtime_seconds, actual_runtime_seconds, non-empty checks, a `figures` list (empty only when figure_specs is empty), and exactly one claim_evidence entry per declared claim. Every figures entry must match the schema in the figure-quality Skill, include the exact planned `spec_id`, `reference_id`, and `required_data_fields`, and point to real data, generator, vector master, PNG preview, supporting claims, style stack, language, and completed checks. Ensure every required data field actually exists in a declared CSV header or JSON key. Each claim_evidence entry needs claim_id, status='supported', method, independent boolean, and non-empty evidence_paths inside the current problem boundary. You are proposing a candidate, not accepting your own work. Run the checks and stop."""


def _review_json_contract(review_type: str, problem_id: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "review_type": review_type,
            "problem_id": problem_id or None,
            "verdict": "accept | reject | blocked",
            "statement_alignment": "pass | fail",
            "method_validity": "pass | fail",
            "implementation_fidelity": "pass | fail",
            "evidence_sufficiency": "pass | fail",
            "issue_class": "none | implementation | method | ambiguity | evidence | blocked",
            "issues": [],
            "required_repairs": [],
        },
        ensure_ascii=False,
        indent=2,
    )


def plan_audit_prompt(evidence_paths: list[str] | None = None) -> str:
    contract = _review_json_contract("plan", "")
    scope = _evidence_scope(evidence_paths)
    return f"""Act as an independent scientific plan auditor. Use this Host-owned evidence scope:
{scope}

Read $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md. Do not trust the planner summary, edit files, run expensive computation, or begin implementation.

Check statement coverage, dependency correctness, assumptions, every approximation or surrogate, claim-specific evidence obligations, failure semantics, independent validation, optimality/event/convergence claims, runtime feasibility, and every figure_specs entry. For continuous/global claims, reject missing finite domains, witnesses, gap/tail exclusions, computable bounds, or realistic nested-operation budgets. Also reject proof inflation: formal root/interval tools must match the mathematical object and cover objective plus inequality constraints; otherwise the claim must be scoped as a numerical estimate with explicit limitations. Do not demand a formal theorem when the problem asks for a numerical modeling answer and a bounded, reproducible, independently challenged estimate is scientifically honest. Reject figure plans whose reference family does not fit the claim, whose panels do not share a scientific context, whose required data fields cannot expose the stated certificate, or whose source-data path is not real problem evidence. Reject plans that can pass by self-reporting, conflate solver failure with a domain event, use an undeclared proxy, or provide validation equivalent to the primary method.

Return only strict JSON matching this shape, with null problem_id. No prose or Markdown fences:
{contract}"""


def plan_revision_prompt(
    audit: dict[str, Any], evidence_paths: list[str] | None = None
) -> str:
    evidence = json.dumps(audit, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    return f"""The independent plan audit rejected execution_plan.json. Use this Host-owned evidence scope:
{scope}

Revise reports/ANALYSIS_MODELING_REPORT.md plus the full schema-v2 execution_plan.json in place. Preserve problem IDs and valid dependency order. Resolve every issue below without writing code or starting execution. Stop after validating both planning artifacts.

Prefer the cheapest scientifically honest repair. Do not answer an audit request for stronger evidence by inventing an inapplicable formal proof or an exhaustive search whose nested operation count exceeds the runtime limit. Instead define finite witnesses/brackets and conservative checks that are executable, narrow an ambiguity with explicit justification, or scope a global-sounding claim as a reproducible numerical estimate with domain, resolution, uncertainty and limitation. Failure conditions must have mutually exclusive meanings. First-event figures must use an event reference and expose real bound/bracket/certificate fields.

Audit evidence:
{evidence}"""


def scientific_review_prompt(
    problem: dict[str, Any],
    evidence_paths: list[str] | None = None,
    figure_references: list[dict[str, Any]] | None = None,
) -> str:
    contract = _review_json_contract("scientific", problem["id"])
    scope = _evidence_scope(evidence_paths)
    reference_payload = json.dumps(figure_references or [], ensure_ascii=False, indent=2)
    return f"""Act as the independent scientific acceptance reviewer for {problem['id']}. Read $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md, then use this Host-owned evidence scope:
{scope}

Use only these selected figure-reference entries and exact `$MATHMODELAGENT_ROOT/<preview_path>` previews:
{reference_payload}

Do not trust candidate status, planner claims, or worker prose. Do not edit/create files or run expensive computations.

Check that the candidate answers the exact requested output, implements the declared method, exposes every approximation, keeps failure semantics correct, supports optimality/event/feasibility claims with evidence at the level promised by the contract, and uses genuinely independent validation. Require formal global proof only when the claim actually promises certification; a bounded numerical estimate may pass when its domain, resolution, uncertainty, convergence and limitations are truthful and independently challenged. Reject either overclaiming or silently weakening the planned claim. Also inspect every `verification.json.figures` entry, its matching Planner `figure_specs` entry, selected reference preview, real source data, generator, vector and rendered preview. Confirm every required_data_fields value exists and carries the plotted bound, bracket, threshold or certificate linkage. The produced figure must preserve the reference's useful explanatory structure without copying its data. Axes, units, scales, uncertainty and sample size must not mislead; bars require a defensible baseline; heatmaps require a meaningful colormap/center; plotted values must agree with frozen candidate evidence; labels and legends must be readable.

Apply this 100-point figure rubric internally to each figure: scientific purpose 20, claim/plot-family fit 20, data and scale integrity 20, visual hierarchy and information density 15, direct annotations 10, color plus grayscale readability 10, and final paper-size readability 5. Any semantic/data/provenance defect is a hard reject regardless of score. Reject scores below 85 with concrete required repairs; 70--84 is a visual-quality repair, not acceptance. Do not add rubric fields to the strict JSON response. A runnable program, attractive plot, and internally consistent numbers are insufficient by themselves.

Return only strict JSON matching this shape. No prose or Markdown fences:
{contract}"""


def scientific_repair_prompt(
    problem: dict[str, Any],
    review: dict[str, Any],
    evidence_paths: list[str] | None = None,
) -> str:
    evidence = json.dumps(review, ensure_ascii=False, indent=2)
    scope = _evidence_scope(evidence_paths)
    return f"""Scientific review rejected candidate {problem['id']} with issue class {review['issue_class']}. Use this Host-owned evidence scope:
{scope}

Repair only code/{problem['id']}/, results/{problem['id']}/, figures/{problem['id']}/, and reports/{problem['id']}_RESULTS.md. Do not alter the scientific contract, input, accepted dependencies, or later problems. Resolve every required repair, rerun the primary and independent checks, regenerate candidate result/verification artifacts, and stop.

Review evidence:
{evidence}"""


def method_replan_prompt(problem_id: str, review: dict[str, Any], downstream_ids: list[str]) -> str:
    evidence = json.dumps(review, ensure_ascii=False, indent=2)
    return f"""Scientific review found a method-level or unresolved ambiguity in {problem_id}. Re-read the original inputs and current execution_plan.json. Write execution_plan.revision.json only; do not edit the active plan, code, results, reports, or accepted upstream artifacts.

The revision must contain schema_version=1, base_plan_version equal to the current plan_version, and revised_problems containing complete schema-v2 contracts for exactly these IDs in order: {downstream_ids}. For ambiguity, evaluate all defensible interpretations; include multiple branches when affordable, otherwise adopt the most conservative interpretation that does not overstate conclusions and record it explicitly. Resolve every review issue.

Review evidence:
{evidence}"""


def _evidence_scope(paths: list[str] | None) -> str:
    items = "\n".join(f"- {path}" for path in (paths or []))
    return f"""Use the following Host-selected stage context. This list guides relevance and performance; it is not a filesystem access-control boundary. Read every listed file needed to discharge the stage contract and skip only clearly irrelevant optional attachments:
{items or '- None'}

Do not inspect Host implementation under $MATHMODELAGENT_ROOT/pi/*.py, tests/, other workspaces, unlisted superseded Method/Spike versions, validator implementations, or repository history. Read only skill/reference files explicitly named by this prompt. Do not use shell/search tools to discover additional context or run Git status/diff. Batch independent reads in one turn. Missing information must be reported rather than recovered from unlisted files."""


def paper_planning_prompt(
    plan_version: int, evidence_paths: list[str] | None = None
) -> str:
    scope = _evidence_scope(evidence_paths)
    return f"""Act as the scientific paper content planner only. Use this Host-owned evidence scope:
{scope}

The active execution plan version is {plan_version}. Do not write paper source or modify accepted artifacts.

Write paper_plan.json and reports/PAPER_PLAN.md. paper_plan.json uses schema_version=1, the active plan_version, an advisory recommended_page_range [min,max], and coverage with exactly one entry per accepted claim. Each coverage entry requires claim_id, problem_id, section_id, interpretation_and_assumptions, non-empty model_or_equations, algorithm_and_stopping, non-empty frozen result_evidence and validation_evidence paths, sensitivity_or_robustness, approximation_ids, non-empty limitations, figures, and citations_needed. Every claim must state its applicability boundary or limitation; every declared approximation must also be disclosed there. Page range is advisory, but choose it proportionally to problem complexity and avoid duplicate sections or padding. Scientific content coverage is mandatory. Stop before writing the paper."""


def paper_plan_repair_prompt(errors: list[str]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"""The deterministic paper-plan coverage gate rejected paper_plan.json. Repair only paper_plan.json and reports/PAPER_PLAN.md, cover every accepted claim and approximation using existing frozen evidence, and stop. Do not write the paper or invent experiments.

Gate evidence:
{details}"""


def paper_manifest_repair_prompt(errors: list[str]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"""The Host rejected the paper or paper/paper_manifest.json. Repair only paper/. Do not modify accepted evidence or paper_plan.json. Fix every gate error, compile the PDF twice, and stop.

The manifest must use exactly this coverage-entry shape (repeat once per paper-plan claim):
{{
  "claim_id": "exact claim id",
  "section_file": "paper/relative-section-file.tex",
  "anchors": {{
    "model": "literal substring present in section source",
    "algorithm": "literal substring present in section source",
    "result": "literal substring present in section source",
    "validation": "literal substring present in section source",
    "conclusion": "literal substring present in section source",
    "limitation": "unique literal substring from an explicit applicability, assumption-risk, or limitation sentence in the section source"
  }},
  "figures": ["existing relative figure path"]
}}
Every anchor must occur exactly once in its section and the six anchors must be distinct. The limitation anchor must identify an actual limitation sentence and must not reuse or overlap the model, algorithm, result, validation, or conclusion anchor. Every explicit `\\bibitem{{key}}` must have at least one matching `\\cite{{key}}` in the body, and every citation key must resolve. Keep exactly one table of contents. Do not force a short reference list onto a separate page with `\\newpage` or `\\clearpage`, and remove consecutive forced page breaks. Do not rename `figures` to `figure_paths`. Top-level fields are schema_version=1, plan_version, and coverage.

For charts, use the paper language for labels unless a standard symbol has no translation. Render the affected PDF pages at readable resolution and inspect them before stopping: axis titles, ticks, legends, annotations, and breakpoint labels must not overlap or clip.

Gate evidence:
{details}"""


def repair_prompt(stage: str, errors: list[str]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"""The deterministic gate rejected stage {stage}. Repair only this stage; do not start later work and do not modify input/, execution_plan.json, reports/ANALYSIS_MODELING_REPORT.md, or completed problem directories.

Gate evidence:
{details}

Fix the root cause, rerun the smallest relevant check, regenerate the current stage artifacts, and stop."""


def review_prompt(problem: dict[str, Any], errors: list[str]) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"""Act as a read-only numerical-method reviewer for {problem['id']}. Read its plan contract, code, results, report, and the gate evidence below. Do not edit or create files and do not run expensive computations.

{details}

Return concise Markdown containing: root cause, whether the method remains valid, exact files/functions the worker should change, a cheaper validation command, and a stopping condition. If the global plan itself must change, say METHOD_INVALID explicitly."""


def final_repair_prompt(problem: dict[str, Any]) -> str:
    return f"""Perform the final bounded repair for {problem['id']} only. Read reports/{problem['id']}_REPAIR_REVIEW.md, the task contract in execution_plan.json, and current code/results. Apply the review without changing input/, execution_plan.json, reports/ANALYSIS_MODELING_REPORT.md, or completed problem directories. Rerun smoke timing and validation, regenerate the required result.json and verification.json, then stop. Do not start later problems."""


def writing_repair_prompt(errors: list[str], repair_number: int = 1) -> str:
    details = "\n".join(f"- {error}" for error in errors)
    return f"""The independent verification stage rejected the paper. This is bounded writing repair {repair_number} of 2. Repair only paper/. Read the complete reports/VERIFY_REPORT.md and the evidence below. Do not modify input/, execution_plan.json, code/, results/, figures/, or accepted problem reports.

{details}

Fix every item under the report's hard-error and required-repairs sections, including truthful manifest anchors. Preserve accepted numbers. For each visual defect, edit the source, compile twice, render the exact failed PDF page at at least 160 DPI, inspect the resulting image, and iterate within this repair turn until all cited labels, axes, legends, tables, and margins are visibly separated and unclipped. Use the paper language for chart prose. Confirm all manifest anchors are unique, semantically correct literal substrings before stopping. Confirm every explicit bibliography item is cited in the body and every citation resolves. Remove duplicate contents pages, consecutive forced page breaks, and forced separation before a short reference list. Use TEST as a neutral control number only when the problem supplied none. Do not run verification or write VERIFY_REPORT.md yourself."""


def final_stage_prompt(
    stage: str,
    *,
    competition: str,
    language: str,
    paper_engine: str,
    evidence_paths: list[str] | None = None,
) -> str:
    if stage == "diagram":
        scope = _evidence_scope(evidence_paths)
        return f"""Fully read $MATHMODELAGENT_ROOT/skills/4drawio/SKILL.md and use this Host-owned evidence scope:
{scope}

Create only conceptual diagrams that add scientific information and are explicitly useful to the paper; do not generate generic roadmaps, decorative architecture diagrams, or duplicate a data figure. Always write reports/DRAWIO_REPORT.md; if no conceptual diagram is needed, explain that there. Do not modify code or accepted results. Stop after this stage."""
    if stage == "writing":
        scope = _evidence_scope(evidence_paths)
        return f"""Fully read $MATHMODELAGENT_ROOT/skills/5writing/SKILL.md and use this Host-owned evidence scope:
{scope}

Write the complete {language} {competition} paper using {paper_engine}. Cover every paper-plan claim with its model/equations, algorithm and stopping rule, result evidence, independent validation, sensitivity/robustness, conclusion, and disclosed limitations. Use only accepted frozen evidence for numerical claims; do not recompute, invent values, redraw data, pad length, or omit evidence because a section is short. Shell commands are for compiling, rendering, and validating the paper only, never repository discovery.

Fully cover every claim, but keep the paper proportional to the problem. Treat recommended_page_range as soft guidance: remove repeated claim-template prose, duplicate contents pages, unnecessary forced page breaks, oversized spacing, excessive precision, and unused diagrams before adding pages. Keep exactly one table of contents. Let a short reference list follow the body naturally instead of forcing it onto a new page. Keep enough derivation and evidence to make each conclusion auditable.

Every chart's prose labels, axis titles, legends, and annotations must use {language}; mathematical symbols may remain standard. Embed the accepted frozen vector master declared by its figure provenance. Do not rebuild a chart in paper/. Keep labels visibly separated from ticks, curves, and each other. Compile twice, render every PDF page at readable resolution, and inspect the page images for clipping, overlap, blank pages, and incoherent whitespace before stopping.

Compile a non-empty PDF under paper/. Also write paper/paper_manifest.json with exactly these top-level fields: schema_version=1, active plan_version, and coverage. coverage must contain exactly one object per paper-plan claim with this shape:
{{
  "claim_id": "exact claim id",
  "section_file": "paper/relative-section-file.tex",
  "anchors": {{
    "model": "literal substring present in that section source",
    "algorithm": "literal substring present in that section source",
    "result": "literal substring present in that section source",
    "validation": "literal substring present in that section source",
    "conclusion": "literal substring present in that section source",
    "limitation": "unique literal substring from an explicit applicability, assumption-risk, or limitation sentence in that section source"
  }},
  "figures": ["existing relative figure path"]
}}
Every anchor must occur exactly once in its section and all six anchors must be distinct. The limitation anchor must identify a real limitation, not a model or conclusion phrase, and must not contain or be contained by another anchor. Every explicit `\\bibitem{{key}}` must be cited by at least one body `\\cite{{key}}`, and no body citation may reference an absent key. Do not include uncited background references. The key is exactly `figures`, not `figure_paths`. Stop after the PDF, manifest, and rendered-page inspection are complete."""
    if stage == "verify":
        scope = _evidence_scope(evidence_paths)
        return """Act as the independent read-only Document Reviewer. Fully read $MATHMODELAGENT_ROOT/skills/6verity/SKILL.md and use this Host-owned evidence scope:
""" + scope + """

This current Host contract overrides any skill instruction to run commands or write a report: do not edit/create files, run compilation, render pages, or use shell commands.

Confirm every accepted claim is substantively covered; all six manifest anchors per claim are unique and truthful; frozen evidence and paper numbers agree; required models, results, independent validations, sensitivity boundaries, conclusions, and limitations are present; every listed reference is cited and every citation resolves; the existing log/PDF establish successful compilation; and every rendered page is readable. For every figure, verify accepted provenance, paper-language labels, units, scales, legends, grayscale distinction, and absence of overlap or clipping. Confirm exactly one contents sequence and no unnecessary forced separation before a short reference list. Short page count alone is a warning, not a hard failure.

Return only strict JSON with exactly this shape and no Markdown fences:
{
  "schema_version": 1,
  "review_type": "document",
  "problem_id": null,
  "verdict": "accept | reject | blocked",
  "claim_coverage": "pass | fail",
  "manifest_anchors": "pass | fail",
  "evidence_consistency": "pass | fail",
  "references_and_figures": "pass | fail",
  "compilation": "pass | fail",
  "visual_readability": "pass | fail",
  "document_structure": "pass | fail",
  "issue_class": "none | content | manifest | evidence | compilation | visual | blocked",
  "summary": "concise evidence-based summary",
  "issues": [],
  "required_repairs": [],
  "warnings": []
}

Use verdict=accept only when all seven checks pass, issue_class=none, and issues/required_repairs are empty. For reject or blocked, provide at least one concrete issue and matching repair with the exact source file, physical PDF page, and affected text or element when available. The Host alone writes reports/VERIFY_REPORT.md and decides completion."""
    raise ValueError(f"unknown final stage: {stage}")

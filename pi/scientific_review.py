"""Scientific acceptance contracts for staged MathModelAgent workflows."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from pi.figure_quality import figure_evidence_errors

CLAIM_TYPES = {
    "identity_or_constraint",
    "feasibility",
    "optimality",
    "event_or_boundary",
    "approximation",
    "estimate_or_prediction",
    "ranking_or_decision",
    "other",
}
REVIEW_CHECKS = (
    "statement_alignment",
    "method_validity",
    "implementation_fidelity",
    "evidence_sufficiency",
)
REVIEW_ISSUES = {"none", "implementation", "method", "ambiguity", "evidence", "blocked"}
METHOD_REVIEW_CHECKS = (
    "statement_alignment",
    "method_validity",
    "computational_feasibility",
    "evidence_calibration",
    "validation_independence",
    "dependency_consistency",
    "figure_contract",
)
METHOD_REVIEW_ISSUES = {"none", "method", "ambiguity", "evidence", "budget", "blocked"}
DOCUMENT_REVIEW_CHECKS = (
    "claim_coverage",
    "manifest_anchors",
    "evidence_consistency",
    "references_and_figures",
    "compilation",
    "visual_readability",
    "document_structure",
)
DOCUMENT_REVIEW_ISSUES = {
    "none", "content", "manifest", "evidence", "compilation", "visual", "blocked"
}
EVIDENCE_LEVELS = {"A_certified", "B_bounded_numerical", "C_exploratory"}
FAILURE_CATEGORIES = {
    "domain_event",
    "mathematical_infeasibility",
    "numerical_failure",
    "data_or_input_failure",
    "decision_outcome",
}
ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PAPER_COVERAGE_EXAMPLE = {
    "claim_id": "exact_claim_id", "problem_id": "q1", "section_id": "q1",
    "interpretation_and_assumptions": "Interpretation and assumptions.",
    "model_or_equations": ["Objective and equations."],
    "algorithm_and_stopping": "Algorithm and stopping rule.",
    "result_evidence": ["results/q1/result.json"],
    "validation_evidence": ["results/q1/evidence.json"],
    "sensitivity_or_robustness": "Sensitivity or justified applicability boundary.",
    "approximation_ids": [], "limitations": ["Explicit applicability limitation."],
    "figures": [], "citations_needed": [],
}


class ScientificContractError(ValueError):
    """A scientific or paper contract is malformed."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScientificContractError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScientificContractError(f"{name} must be a non-empty string")
    return value.strip()


def _list(value: Any, name: str, *, empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not empty and not value):
        qualifier = "a list" if empty else "a non-empty list"
        raise ScientificContractError(f"{name} must be {qualifier}")
    return value


def _text_list(value: Any, name: str, *, empty: bool = False) -> list[str]:
    return [_text(item, f"{name}[]") for item in _list(value, name, empty=empty)]


def _id(value: Any, name: str) -> str:
    result = _text(value, name)
    if not ID_RE.fullmatch(result):
        raise ScientificContractError(f"{name} is invalid: {result!r}")
    return result


def _relative(value: Any, name: str) -> str:
    raw = _text(value, name).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ScientificContractError(f"{name} is not a safe relative path: {raw}")
    return path.as_posix()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_problem_science(
    problem: dict[str, Any],
    problem_id: str,
    *,
    required_output_ids: set[str] | None = None,
) -> dict[str, Any]:
    requested = _text_list(problem.get("requested_outputs"), f"{problem_id}.requested_outputs")
    interpretation = _text(problem.get("interpretation"), f"{problem_id}.interpretation")

    assumptions = []
    assumption_ids: set[str] = set()
    for index, raw in enumerate(_list(problem.get("assumptions"), f"{problem_id}.assumptions", empty=True)):
        item = _object(raw, f"{problem_id}.assumptions[{index}]")
        assumption_id = _id(item.get("id"), f"{problem_id}.assumptions[{index}].id")
        if assumption_id in assumption_ids:
            raise ScientificContractError(f"duplicate assumption id: {assumption_id}")
        assumption_ids.add(assumption_id)
        assumptions.append({
            "id": assumption_id,
            "statement": _text(item.get("statement"), f"{assumption_id}.statement"),
            "rationale": _text(item.get("rationale"), f"{assumption_id}.rationale"),
            "validation": _text(item.get("validation"), f"{assumption_id}.validation"),
        })

    claims = []
    claim_ids: set[str] = set()
    for index, raw in enumerate(_list(problem.get("claims"), f"{problem_id}.claims")):
        item = _object(raw, f"{problem_id}.claims[{index}]")
        claim_id = _id(item.get("id"), f"{problem_id}.claims[{index}].id")
        if claim_id in claim_ids:
            raise ScientificContractError(f"duplicate claim id: {claim_id}")
        claim_type = _text(item.get("type"), f"{claim_id}.type")
        if claim_type not in CLAIM_TYPES:
            raise ScientificContractError(f"unsupported claim type: {claim_type}")
        acceptance = _object(item.get("acceptance"), f"{claim_id}.acceptance")
        tolerance = acceptance.get("tolerance")
        if tolerance is not None and (
            isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(float(tolerance))
            or tolerance < 0
        ):
            raise ScientificContractError(f"{claim_id}.acceptance.tolerance must be null or nonnegative finite number")
        claim_ids.add(claim_id)
        claim = {
            "id": claim_id,
            "type": claim_type,
            "statement": _text(item.get("statement"), f"{claim_id}.statement"),
            "evidence_required": _text_list(item.get("evidence_required"), f"{claim_id}.evidence_required"),
            "acceptance": {
                "criterion": _text(acceptance.get("criterion"), f"{claim_id}.acceptance.criterion"),
                "tolerance": tolerance,
            },
        }
        if "evidence_level" in item or required_output_ids is not None:
            evidence_level = _text(item.get("evidence_level"), f"{claim_id}.evidence_level")
            if evidence_level not in EVIDENCE_LEVELS:
                raise ScientificContractError(f"unsupported evidence level: {evidence_level}")
            output_ids = _text_list(
                item.get("requested_output_ids"),
                f"{claim_id}.requested_output_ids",
                empty=True,
            )
            if required_output_ids is not None:
                unknown_outputs = set(output_ids) - required_output_ids
                if unknown_outputs:
                    raise ScientificContractError(
                        f"{claim_id} references unknown requested outputs: {sorted(unknown_outputs)}"
                    )
                if evidence_level == "C_exploratory" and output_ids:
                    raise ScientificContractError(
                        f"{claim_id} exploratory evidence cannot cover requested outputs"
                    )
            claim.update({
                "evidence_level": evidence_level,
                "requested_output_ids": output_ids,
                "limitations": _text_list(
                    item.get("limitations"), f"{claim_id}.limitations", empty=True
                ),
            })
            if evidence_level in {"B_bounded_numerical", "C_exploratory"} and not claim["limitations"]:
                raise ScientificContractError(
                    f"{claim_id}.limitations must be non-empty for bounded/exploratory evidence"
                )
        claims.append(claim)

    approximations = []
    approximation_ids: set[str] = set()
    for index, raw in enumerate(_list(problem.get("approximations"), f"{problem_id}.approximations", empty=True)):
        item = _object(raw, f"{problem_id}.approximations[{index}]")
        approximation_id = _id(item.get("id"), f"{problem_id}.approximations[{index}].id")
        if approximation_id in approximation_ids:
            raise ScientificContractError(f"duplicate approximation id: {approximation_id}")
        approximation_ids.add(approximation_id)
        approximations.append({
            "id": approximation_id,
            "original_quantity": _text(item.get("original_quantity"), f"{approximation_id}.original_quantity"),
            "surrogate_quantity": _text(item.get("surrogate_quantity"), f"{approximation_id}.surrogate_quantity"),
            "justification": _text(item.get("justification"), f"{approximation_id}.justification"),
            "error_or_equivalence_check": _text(item.get("error_or_equivalence_check"), f"{approximation_id}.error_or_equivalence_check"),
        })

    failure_semantics = []
    failure_conditions: dict[str, str] = {}
    failure_ids: set[str] = set()
    event_categories: dict[str, str] = {}
    for index, raw in enumerate(_list(problem.get("failure_semantics"), f"{problem_id}.failure_semantics")):
        item = _object(raw, f"{problem_id}.failure_semantics[{index}]")
        if required_output_ids is not None and set(item) != {
            "id", "event_id", "category", "condition", "action"
        }:
            raise ScientificContractError(
                f"failure_semantics[{index}] v3 keys mismatch"
            )
        condition = _text(item.get("condition"), f"failure_semantics[{index}].condition")
        classification = (
            _text(item.get("category"), f"failure_semantics[{index}].category")
            if required_output_ids is not None
            else _text(item.get("classification"), f"failure_semantics[{index}].classification")
        )
        condition_key = " ".join(condition.casefold().split())
        if condition_key in failure_conditions:
            raise ScientificContractError(
                f"duplicate failure condition has ambiguous classifications: {condition!r}"
            )
        normalized_failure = {
            "condition": condition,
            "action": _text(item.get("action"), f"failure_semantics[{index}].action"),
        }
        if required_output_ids is None:
            normalized_failure["classification"] = classification
        if required_output_ids is not None:
            failure_id = _id(item.get("id"), f"failure_semantics[{index}].id")
            event_id = _id(item.get("event_id"), f"failure_semantics[{index}].event_id")
            category = _text(item.get("category"), f"failure_semantics[{index}].category")
            if failure_id in failure_ids:
                raise ScientificContractError(f"duplicate failure semantic id: {failure_id}")
            if category not in FAILURE_CATEGORIES:
                raise ScientificContractError(f"unsupported failure category: {category}")
            if event_id in event_categories and event_categories[event_id] != category:
                raise ScientificContractError(
                    f"failure event {event_id} has conflicting categories"
                )
            failure_ids.add(failure_id)
            event_categories[event_id] = category
            normalized_failure.update({
                "id": failure_id,
                "event_id": event_id,
                "category": category,
            })
        failure_conditions[condition_key] = classification
        failure_semantics.append(normalized_failure)

    validations = []
    validation_ids: set[str] = set()
    covered: set[str] = set()
    for index, raw in enumerate(_list(problem.get("independent_validation"), f"{problem_id}.independent_validation")):
        item = _object(raw, f"{problem_id}.independent_validation[{index}]")
        validation_claims = _text_list(item.get("claims"), f"independent_validation[{index}].claims")
        unknown = set(validation_claims) - claim_ids
        if unknown:
            raise ScientificContractError(f"independent validation references unknown claims: {sorted(unknown)}")
        covered.update(validation_claims)
        validation_id = _id(item.get("id"), f"independent_validation[{index}].id")
        if validation_id in validation_ids:
            raise ScientificContractError(f"duplicate independent validation id: {validation_id}")
        validation_ids.add(validation_id)
        validations.append({
            "id": validation_id,
            "method": _text(item.get("method"), f"independent_validation[{index}].method"),
            "independent_from": _text(item.get("independent_from"), f"independent_validation[{index}].independent_from"),
            "claims": validation_claims,
        })
    if covered != claim_ids:
        raise ScientificContractError(f"independent validation does not cover claims: {sorted(claim_ids - covered)}")
    if required_output_ids is not None:
        output_coverage = {
            output_id
            for claim in claims
            if claim.get("evidence_level") in {"A_certified", "B_bounded_numerical"}
            for output_id in claim.get("requested_output_ids", [])
        }
        if output_coverage != required_output_ids:
            raise ScientificContractError(
                f"requested outputs lack Level A/B claim coverage: {sorted(required_output_ids - output_coverage)}"
            )

    return {
        "requested_outputs": requested,
        "interpretation": interpretation,
        "assumptions": assumptions,
        "claims": claims,
        "approximations": approximations,
        "failure_semantics": failure_semantics,
        "independent_validation": validations,
    }


def parse_review(text: str, *, review_type: str, problem_id: str | None = None) -> dict[str, Any]:
    """Parse a strict, prose-free reviewer response."""
    stripped = text.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        stripped = stripped[7:-3].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ScientificContractError(f"review is not strict JSON: {exc.msg}") from exc
    item = _object(value, "review")
    expected = {
        "schema_version",
        "review_type",
        "problem_id",
        "verdict",
        *REVIEW_CHECKS,
        "issue_class",
        "issues",
        "required_repairs",
    }
    unknown = set(item) - expected
    missing = expected - set(item)
    if unknown or missing:
        raise ScientificContractError(f"review keys mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}")
    if item["schema_version"] != 1 or item["review_type"] != review_type:
        raise ScientificContractError("review schema_version/review_type mismatch")
    actual_problem = item["problem_id"]
    if actual_problem is not None and not isinstance(actual_problem, str):
        raise ScientificContractError("review.problem_id must be string or null")
    if problem_id != actual_problem:
        raise ScientificContractError("review.problem_id mismatch")
    verdict = item["verdict"]
    if verdict not in {"accept", "reject", "blocked"}:
        raise ScientificContractError("review.verdict is invalid")
    checks = {name: item[name] for name in REVIEW_CHECKS}
    if any(value not in {"pass", "fail"} for value in checks.values()):
        raise ScientificContractError("review checks must be pass or fail")
    issue_class = item["issue_class"]
    if issue_class not in REVIEW_ISSUES:
        raise ScientificContractError("review.issue_class is invalid")
    issues = _text_list(item["issues"], "review.issues", empty=True)
    repairs = _text_list(item["required_repairs"], "review.required_repairs", empty=True)
    if verdict == "accept":
        if any(value != "pass" for value in checks.values()) or issue_class != "none" or issues or repairs:
            raise ScientificContractError("accepted review must have all-pass checks and no issues")
    elif issue_class == "none" or not issues:
        raise ScientificContractError("non-accepted review requires issue_class and issues")
    return {
        "schema_version": 1,
        "review_type": review_type,
        "problem_id": actual_problem,
        "verdict": verdict,
        **checks,
        "issue_class": issue_class,
        "issues": issues,
        "required_repairs": repairs,
    }


def parse_document_review(text: str) -> dict[str, Any]:
    """Parse a strict, read-only Document Verification verdict."""
    stripped = text.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        stripped = stripped[7:-3].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ScientificContractError(
            f"document review is not strict JSON: {exc.msg}"
        ) from exc
    item = _object(value, "document review")
    expected = {
        "schema_version", "review_type", "problem_id", "verdict",
        *DOCUMENT_REVIEW_CHECKS, "issue_class", "summary", "issues",
        "required_repairs", "warnings",
    }
    unknown = set(item) - expected
    missing = expected - set(item)
    if unknown or missing:
        raise ScientificContractError(
            f"document review keys mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if (
        item["schema_version"] != 1
        or item["review_type"] != "document"
        or item["problem_id"] is not None
    ):
        raise ScientificContractError("document review identity mismatch")
    verdict = item["verdict"]
    if verdict not in {"accept", "reject", "blocked"}:
        raise ScientificContractError("document review verdict is invalid")
    checks = {name: item[name] for name in DOCUMENT_REVIEW_CHECKS}
    if any(value not in {"pass", "fail"} for value in checks.values()):
        raise ScientificContractError("document review checks must be pass or fail")
    issue_class = item["issue_class"]
    if issue_class not in DOCUMENT_REVIEW_ISSUES:
        raise ScientificContractError("document review issue_class is invalid")
    summary = _text(item["summary"], "document review.summary")
    issues = _text_list(item["issues"], "document review.issues", empty=True)
    repairs = _text_list(
        item["required_repairs"], "document review.required_repairs", empty=True
    )
    warnings = _text_list(item["warnings"], "document review.warnings", empty=True)
    if verdict == "accept":
        if (
            any(value != "pass" for value in checks.values())
            or issue_class != "none"
            or issues
            or repairs
        ):
            raise ScientificContractError(
                "accepted document review must have all-pass checks and no issues"
            )
    elif issue_class == "none" or not issues or not repairs:
        raise ScientificContractError(
            "non-accepted document review requires issue_class, issues, and repairs"
        )
    return {
        "schema_version": 1,
        "review_type": "document",
        "problem_id": None,
        "verdict": verdict,
        **checks,
        "issue_class": issue_class,
        "summary": summary,
        "issues": issues,
        "required_repairs": repairs,
        "warnings": warnings,
    }


def document_review_markdown(
    review: dict[str, Any], host_errors: list[str] | None = None
) -> str:
    """Render the Host-owned verification report from validated JSON."""
    host_errors = host_errors or []
    one_line = lambda value: " ".join(str(value).split())
    passed = review["verdict"] == "accept" and not host_errors
    lines = [
        "# Document Verification Report",
        "",
        "## Conclusion",
        "",
        "PASS" if passed else "FAIL",
        "",
        "## Summary",
        "",
        one_line(review["summary"]),
        "",
        "## Checks",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{name}` | {review[name].upper()} |" for name in DOCUMENT_REVIEW_CHECKS
    )
    sections = (
        ("Host gate errors", host_errors),
        ("Issues", review["issues"]),
        ("Required repairs", review["required_repairs"]),
        ("Warnings", review["warnings"]),
    )
    for title, values in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(
            (f"- {one_line(value)}" for value in values) if values else ["None."]
        )
    return "\n".join(lines) + "\n"


def parse_method_review(text: str, *, problem_id: str) -> dict[str, Any]:
    """Parse the strict read-only method-audit response."""
    stripped = text.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        stripped = stripped[7:-3].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ScientificContractError(f"review is not strict JSON: {exc.msg}") from exc
    item = _object(value, "method review")
    expected = {
        "schema_version",
        "review_type",
        "problem_id",
        "verdict",
        *METHOD_REVIEW_CHECKS,
        "issue_class",
        "issues",
        "required_repairs",
        "supplemental_spike",
        "supplemental_spike_ids",
        "allowed_downgrades",
    }
    unknown = set(item) - expected
    missing = expected - set(item)
    if unknown or missing:
        raise ScientificContractError(
            f"method review keys mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if (
        item["schema_version"] != 1
        or item["review_type"] != "method"
        or item["problem_id"] != problem_id
    ):
        raise ScientificContractError("method review identity mismatch")
    verdict = item["verdict"]
    if verdict not in {"accept", "revise", "blocked"}:
        raise ScientificContractError("method review verdict is invalid")
    checks = {name: item[name] for name in METHOD_REVIEW_CHECKS}
    if any(value not in {"pass", "fail"} for value in checks.values()):
        raise ScientificContractError("method review checks must be pass or fail")
    issue_class = item["issue_class"]
    if issue_class not in METHOD_REVIEW_ISSUES:
        raise ScientificContractError("method review issue_class is invalid")
    issues = _text_list(item["issues"], "method review.issues", empty=True)
    repairs = _text_list(item["required_repairs"], "method review.required_repairs", empty=True)
    if not isinstance(item["supplemental_spike"], bool):
        raise ScientificContractError("method review supplemental_spike must be boolean")
    supplemental_ids = _text_list(
        item["supplemental_spike_ids"],
        "method review.supplemental_spike_ids",
        empty=True,
    )
    if item["supplemental_spike"] != bool(supplemental_ids):
        raise ScientificContractError(
            "supplemental_spike must match non-empty supplemental_spike_ids"
        )
    downgrades = []
    for index, raw in enumerate(
        _list(item["allowed_downgrades"], "allowed_downgrades", empty=True)
    ):
        downgrade = _object(raw, f"allowed_downgrades[{index}]")
        if set(downgrade) != {"claim_id", "from", "to", "reason"}:
            raise ScientificContractError("allowed downgrade keys mismatch")
        if downgrade["from"] != "A_certified" or downgrade["to"] != "B_bounded_numerical":
            raise ScientificContractError("only A_certified to B_bounded_numerical is allowed")
        downgrades.append({
            "claim_id": _id(downgrade["claim_id"], "downgrade.claim_id"),
            "from": downgrade["from"],
            "to": downgrade["to"],
            "reason": _text(downgrade["reason"], "downgrade.reason"),
        })
    if verdict == "accept":
        if (
            any(value != "pass" for value in checks.values())
            or issue_class != "none"
            or issues
            or repairs
            or item["supplemental_spike"]
            or supplemental_ids
            or downgrades
        ):
            raise ScientificContractError("accepted method review must be all-pass")
    elif issue_class == "none" or not issues:
        raise ScientificContractError("non-accepted method review requires issues")
    if verdict == "blocked" and issue_class != "blocked":
        raise ScientificContractError("blocked method review requires blocked issue_class")
    return {
        "schema_version": 1,
        "review_type": "method",
        "problem_id": problem_id,
        "verdict": verdict,
        **checks,
        "issue_class": issue_class,
        "issues": issues,
        "required_repairs": repairs,
        "supplemental_spike": item["supplemental_spike"],
        "supplemental_spike_ids": supplemental_ids,
        "allowed_downgrades": downgrades,
    }


def candidate_errors(workspace: Path, problem: dict[str, Any]) -> list[str]:
    """Validate candidate claim evidence without granting scientific acceptance."""
    problem_id = problem["id"]
    errors: list[str] = []
    result = _json_file(workspace / "results" / problem_id / "result.json", errors)
    verification = _json_file(workspace / "results" / problem_id / "verification.json", errors)
    if result is not None:
        if result.get("problem_id") != problem_id:
            errors.append("candidate_protocol: result problem_id mismatch")
        if result.get("status") != "candidate":
            errors.append("candidate_protocol: worker may only report status=candidate")
        metrics = result.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            errors.append("candidate_protocol: result metrics must be non-empty")
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
                    errors.append(f"candidate_protocol: invalid metric {index}")
    if verification is None:
        return errors
    if verification.get("schema_version") != 2 or verification.get("status") != "candidate":
        errors.append("candidate_protocol: verification schema/status")
    for field in ("smoke_runtime_seconds", "estimated_runtime_seconds", "actual_runtime_seconds"):
        value = verification.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"candidate_protocol: invalid {field}")
    if not isinstance(verification.get("checks"), list) or not verification["checks"]:
        errors.append("candidate_protocol: checks must be non-empty")
    errors.extend(figure_evidence_errors(workspace, problem, verification))
    expected = {claim["id"] for claim in problem["claims"]}
    evidence = verification.get("claim_evidence")
    if not isinstance(evidence, list):
        errors.append("candidate_protocol: claim_evidence must be a list")
        return errors
    seen: set[str] = set()
    allowed = (
        f"code/{problem_id}/",
        f"results/{problem_id}/",
        f"figures/{problem_id}/",
        f"reports/{problem_id}_RESULTS.md",
    )
    for index, raw in enumerate(evidence):
        if not isinstance(raw, dict):
            errors.append(f"candidate_protocol: claim_evidence[{index}] must be object")
            continue
        claim_id = raw.get("claim_id")
        if claim_id not in expected or claim_id in seen:
            errors.append(f"candidate_protocol: invalid or duplicate claim_id {claim_id!r}")
            continue
        seen.add(claim_id)
        if raw.get("status") != "supported":
            errors.append(f"candidate_protocol: {claim_id} is not supported")
        if not isinstance(raw.get("independent"), bool):
            errors.append(f"candidate_protocol: {claim_id}.independent must be boolean")
        if not isinstance(raw.get("method"), str) or not raw["method"].strip():
            errors.append(f"candidate_protocol: {claim_id}.method missing")
        paths = raw.get("evidence_paths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"candidate_protocol: {claim_id}.evidence_paths missing")
            continue
        for value in paths:
            try:
                path = _relative(value, f"{claim_id}.evidence_path")
            except ScientificContractError as exc:
                errors.append(f"candidate_protocol: {exc}")
                continue
            if not path.startswith(allowed):
                errors.append(f"candidate_protocol: evidence outside current problem: {path}")
            elif not (workspace / path).is_file():
                errors.append(f"artifact_missing: {path}")
    missing = expected - seen
    if missing:
        errors.append(f"candidate_protocol: missing claim evidence {sorted(missing)}")
    return errors


def _json_file(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"artifact_missing: {path.as_posix()}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"candidate_protocol: invalid JSON {path.name}: {exc.msg}")
        return None
    if not isinstance(value, dict):
        errors.append(f"candidate_protocol: {path.name} must be object")
        return None
    return value


def merge_plan_revision(base: dict[str, Any], revision: dict[str, Any], current_id: str) -> dict[str, Any]:
    if revision.get("schema_version") != 1 or revision.get("base_plan_version") != base.get("plan_version"):
        raise ScientificContractError("revision schema/base_plan_version mismatch")
    revised = revision.get("revised_problems")
    if not isinstance(revised, list) or not revised:
        raise ScientificContractError("revision requires revised_problems")
    problems = base["problems"]
    ids = [problem["id"] for problem in problems]
    if current_id not in ids:
        raise ScientificContractError("revision current problem missing")
    start = ids.index(current_id)
    revised_ids = [item.get("id") if isinstance(item, dict) else None for item in revised]
    if revised_ids != ids[start:]:
        raise ScientificContractError("revision must replace current and all unexecuted downstream problems in order")
    merged = {**base, "plan_version": int(base.get("plan_version") or 1) + 1}
    merged["problems"] = problems[:start] + revised
    return merged


def plan_completeness_receipt(
    workspace: Path, plan: dict[str, Any], ledger: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Build the deterministic v3 acceptance receipt from current hashes."""
    errors: list[str] = []
    problem_ids = [problem["id"] for problem in plan.get("problems", [])]
    inventory = ledger.get("inventory") if isinstance(ledger, dict) else None
    if not isinstance(inventory, dict) or inventory.get("status") != "accepted":
        errors.append("plan_completeness: inventory is not accepted")
        inventory = {}
    inventory_hash = str(inventory.get("sha256") or "")
    inventory_audit_hash = ""
    inventory_path = workspace / str(inventory.get("path") or "")
    inventory_problems: list[dict[str, Any]] = []
    if not inventory_hash or not inventory_path.is_file() or hashlib.sha256(inventory_path.read_bytes()).hexdigest() != inventory_hash:
        errors.append("plan_completeness: inventory hash mismatch")
    else:
        try:
            inventory_value = json.loads(inventory_path.read_text(encoding="utf-8"))
            raw_problems = inventory_value.get("problems")
            if not isinstance(raw_problems, list):
                raise ValueError("problems must be a list")
            inventory_problems = [
                problem for problem in raw_problems if isinstance(problem, dict)
            ]
            if len(inventory_problems) != len(raw_problems):
                raise ValueError("problem entry is not an object")
        except (OSError, ValueError, json.JSONDecodeError, AttributeError) as exc:
            errors.append(f"plan_completeness: invalid accepted inventory: {exc}")
    inventory_audit = inventory.get("audit")
    if not isinstance(inventory_audit, dict):
        errors.append("plan_completeness: inventory audit is missing")
    else:
        audit_path = workspace / str(inventory_audit.get("path") or "")
        inventory_audit_hash = hashlib.sha256(audit_path.read_bytes()).hexdigest() if audit_path.is_file() else ""
        if inventory_audit_hash != str(inventory_audit.get("sha256") or ""):
            errors.append("plan_completeness: inventory audit hash mismatch")
        else:
            try:
                audit = parse_review(
                    audit_path.read_text(encoding="utf-8"),
                    review_type="inventory",
                    problem_id=None,
                )
                if audit["verdict"] != "accept":
                    errors.append("plan_completeness: inventory audit not accepted")
            except (OSError, ScientificContractError) as exc:
                errors.append(f"plan_completeness: invalid inventory audit: {exc}")
    methods = ledger.get("problems") if isinstance(ledger, dict) else None
    if not isinstance(methods, dict):
        errors.append("plan_completeness: method ledger is invalid")
        methods = {}
    method_hashes: dict[str, str] = {}
    method_report_hashes: dict[str, str] = {}
    audit_hashes: dict[str, str] = {}
    spike_hashes: dict[str, dict[str, str]] = {}
    supplemental_spike_hashes: dict[str, dict[str, str]] = {}
    review_hashes: dict[str, str] = {}
    coverage: dict[str, list[str]] = {}
    for problem in plan.get("problems", []):
        problem_id = problem["id"]
        entry = methods.get(problem_id)
        if not isinstance(entry, dict) or entry.get("status") != "accepted":
            errors.append(f"plan_completeness: {problem_id} method is not accepted")
            entry = {}
        if str(entry.get("plan_problem_sha256") or "") != _canonical_hash(problem):
            errors.append(f"plan_completeness: {problem_id} execution plan differs from active method card")
        for field, target in (
            ("method_card", method_hashes),
            ("method_report", method_report_hashes),
            ("method_audit", audit_hashes),
        ):
            record = entry.get(field)
            if not isinstance(record, dict):
                errors.append(f"plan_completeness: {problem_id} missing {field}")
                continue
            path = workspace / str(record.get("path") or "")
            expected = str(record.get("sha256") or "")
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
            if not expected or actual != expected:
                errors.append(f"plan_completeness: {problem_id} {field} hash mismatch")
            elif field == "method_audit":
                try:
                    method_audit = parse_method_review(
                        path.read_text(encoding="utf-8"), problem_id=problem_id
                    )
                    if method_audit["verdict"] != "accept":
                        errors.append(f"plan_completeness: {problem_id} method audit rejected")
                except (OSError, ScientificContractError) as exc:
                    errors.append(f"plan_completeness: invalid {problem_id} method audit: {exc}")
            target[problem_id] = actual
        for field, target, required in (
            ("spike", spike_hashes, True),
            ("supplemental_spike", supplemental_spike_hashes, bool(entry.get("supplemental_used"))),
        ):
            record = entry.get(field)
            if not isinstance(record, dict):
                if required:
                    errors.append(f"plan_completeness: {problem_id} missing {field}")
                continue
            artifact_hashes = record.get("artifact_sha256")
            if not isinstance(artifact_hashes, dict) or not artifact_hashes:
                errors.append(f"plan_completeness: {problem_id} {field} manifest missing")
                continue
            actual_hashes: dict[str, str] = {}
            for relative, expected_hash in artifact_hashes.items():
                path = workspace / str(relative)
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
                actual_hashes[str(relative)] = actual_hash
                if actual_hash != expected_hash:
                    errors.append(f"plan_completeness: {problem_id} {field} artifact hash mismatch: {relative}")
            report_record = record.get("report")
            report_path = workspace / str((report_record or {}).get("path") or "")
            try:
                spike_report = json.loads(report_path.read_text(encoding="utf-8"))
                if spike_report.get("method_spec_sha256") != entry.get("method_spec_sha256"):
                    errors.append(f"plan_completeness: {problem_id} {field} method-spec hash mismatch")
                declared = set(spike_report.get("artifact_paths") or [])
                report_relative = report_path.relative_to(workspace).as_posix()
                if str((report_record or {}).get("sha256") or "") != actual_hashes.get(report_relative):
                    errors.append(f"plan_completeness: {problem_id} {field} report record mismatch")
                if set(actual_hashes) != declared | {report_relative}:
                    errors.append(f"plan_completeness: {problem_id} {field} manifest coverage mismatch")
            except (OSError, ValueError, json.JSONDecodeError, AttributeError) as exc:
                errors.append(f"plan_completeness: invalid {problem_id} {field} report: {exc}")
            target[problem_id] = actual_hashes
        review_path = workspace / "reports" / f"{problem_id}_SCIENTIFIC_REVIEW.json"
        try:
            review = parse_review(
                review_path.read_text(encoding="utf-8"),
                review_type="scientific",
                problem_id=problem_id,
            )
            if review["verdict"] != "accept":
                errors.append(f"plan_completeness: {problem_id} scientific review rejected")
        except (OSError, ScientificContractError) as exc:
            errors.append(f"plan_completeness: invalid {problem_id} scientific review: {exc}")
        review_hashes[problem_id] = (
            hashlib.sha256(review_path.read_bytes()).hexdigest() if review_path.is_file() else ""
        )
        requested = {
            str(item.get("id") or "")
            for item in problem.get("requested_output_map", [])
            if isinstance(item, dict)
        }
        covered = {
            output_id
            for claim in problem.get("claims", [])
            if claim.get("evidence_level") in {"A_certified", "B_bounded_numerical"}
            for output_id in claim.get("requested_output_ids", [])
        }
        if requested and covered != requested:
            errors.append(
                f"plan_completeness: {problem_id} requested outputs lack Level A/B coverage"
            )
        coverage[problem_id] = sorted(covered)
    if set(methods) != set(problem_ids):
        errors.append("plan_completeness: inventory/plan/method problem IDs differ")
    inventory_ids = [str(problem.get("id") or "") for problem in inventory_problems]
    if inventory_ids != problem_ids:
        errors.append("plan_completeness: accepted inventory and plan problem order differ")
    else:
        for inventory_problem, plan_problem in zip(inventory_problems, plan["problems"]):
            if inventory_problem.get("requested_outputs") != plan_problem.get("requested_output_map"):
                errors.append(
                    f"plan_completeness: {plan_problem['id']} requested outputs differ from inventory"
                )
    receipt = {
        "schema_version": 1,
        "contract_version": 3,
        "status": "pass" if not errors else "fail",
        "plan_version": plan.get("plan_version"),
        "problem_ids": problem_ids,
        "inventory_sha256": inventory_hash,
        "inventory_audit_sha256": inventory_audit_hash,
        "method_card_sha256": method_hashes,
        "method_report_sha256": method_report_hashes,
        "method_audit_sha256": audit_hashes,
        "spike_artifact_sha256": spike_hashes,
        "supplemental_spike_artifact_sha256": supplemental_spike_hashes,
        "scientific_review_sha256": review_hashes,
        "requested_output_coverage": coverage,
    }
    return receipt, errors


def acceptance_chain_errors(
    workspace: Path,
    plan: dict[str, Any],
    *,
    contract_version: int = 2,
    ledger: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if contract_version == 3:
        expected, errors = plan_completeness_receipt(workspace, plan, ledger or {})
        try:
            actual = json.loads(
                (workspace / "reports" / "PLAN_COMPLETENESS.json").read_text(encoding="utf-8")
            )
            if actual != expected or actual.get("status") != "pass":
                errors.append("scientific_acceptance: plan completeness receipt mismatch")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            errors.append(f"scientific_acceptance: invalid completeness receipt: {exc}")
        return errors
    try:
        audit_text = (workspace / "reports" / "PLAN_AUDIT.json").read_text(encoding="utf-8")
        audit = parse_review(audit_text, review_type="plan", problem_id=None)
        if audit["verdict"] != "accept":
            errors.append("scientific_acceptance: plan audit not accepted")
    except (OSError, ScientificContractError) as exc:
        errors.append(f"scientific_acceptance: invalid plan audit: {exc}")
    for problem in plan["problems"]:
        try:
            path = workspace / "reports" / f"{problem['id']}_SCIENTIFIC_REVIEW.json"
            review = parse_review(
                path.read_text(encoding="utf-8"),
                review_type="scientific",
                problem_id=problem["id"],
            )
            if review["verdict"] != "accept":
                errors.append(f"scientific_acceptance: {problem['id']} not accepted")
        except (OSError, ScientificContractError) as exc:
            errors.append(f"scientific_acceptance: invalid {problem['id']} review: {exc}")
    return errors


def paper_evidence_paths(frozen: dict[str, Any]) -> dict[str, list[str]]:
    return {
        problem_id: sorted(
            path for path in paths
            if path.startswith((f"results/{problem_id}/", f"figures/{problem_id}/", f"code/{problem_id}/"))
            or path == f"reports/{problem_id}_RESULTS.md"
        )
        for problem_id, paths in frozen.items() if isinstance(paths, dict)
    }


def paper_plan_frozen_errors(
    paper_plan: dict[str, Any], frozen: dict[str, Any], *, strict: bool = False
) -> list[str]:
    frozen_paths = {
        path
        for problem_paths in frozen.values()
        if isinstance(problem_paths, dict)
        for path in problem_paths
    }
    referenced = {
        path
        for entry in paper_plan["coverage"]
        for field in ("result_evidence", "validation_evidence", "figures")
        for path in entry[field]
    }
    errors = [
        f"paper_coverage: evidence is not frozen: {path}"
        for path in sorted(referenced - frozen_paths)
    ]
    if strict:
        eligible = paper_evidence_paths(frozen)
        for entry in paper_plan["coverage"]:
            allowed = set(eligible.get(entry["problem_id"], []))
            for field in ("result_evidence", "validation_evidence", "figures"):
                for path in entry[field]:
                    if path not in allowed:
                        errors.append(f"paper_coverage: {entry['claim_id']}.{field} is not eligible scientific evidence: {path}")
    return errors


def validate_paper_plan(
    workspace: Path, plan: dict[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    try:
        raw = json.loads((workspace / "paper_plan.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ScientificContractError(f"paper_plan.json missing or invalid: {exc}") from exc
    item = _object(raw, "paper_plan")
    if item.get("schema_version") != 1 or item.get("plan_version") != plan.get("plan_version"):
        raise ScientificContractError("paper plan schema/plan version mismatch")
    page_range = item.get("recommended_page_range")
    if not (
        isinstance(page_range, list)
        and len(page_range) == 2
        and all(isinstance(value, int) and value > 0 for value in page_range)
        and page_range[0] <= page_range[1]
    ):
        raise ScientificContractError("recommended_page_range must be advisory [min,max]")
    expected_claims = {
        claim["id"]: (problem["id"], {approximation["id"] for approximation in problem["approximations"]})
        for problem in plan["problems"]
        for claim in problem["claims"]
    }
    expected_approximations = {
        approximation["id"]
        for problem in plan["problems"]
        for approximation in problem["approximations"]
    }
    disclosed_approximations: set[str] = set()
    coverage = _list(item.get("coverage"), "paper_plan.coverage")
    seen: set[str] = set()
    normalized = []
    format_errors: list[str] = []
    for index, raw_entry in enumerate(coverage):
        if not isinstance(raw_entry, dict):
            format_errors.append(f"paper_plan.coverage[{index}] must be an object")
            continue
        label = raw_entry.get("claim_id") or f"coverage[{index}]"
        for field, example in PAPER_COVERAGE_EXAMPLE.items():
            try:
                if isinstance(example, list):
                    _text_list(raw_entry.get(field), f"{label}.{field}", empty=not example)
                else:
                    _text(raw_entry.get(field), f"{label}.{field}")
            except ScientificContractError as exc:
                format_errors.append(str(exc))
    if format_errors:
        raise ScientificContractError("; ".join(format_errors))
    for index, raw_entry in enumerate(coverage):
        entry = _object(raw_entry, f"paper_plan.coverage[{index}]")
        claim_id = _text(entry.get("claim_id"), f"coverage[{index}].claim_id")
        if claim_id not in expected_claims or claim_id in seen:
            raise ScientificContractError(f"paper plan unknown or duplicate claim: {claim_id}")
        problem_id, approximation_ids = expected_claims[claim_id]
        if entry.get("problem_id") != problem_id:
            raise ScientificContractError(f"paper plan problem mismatch for {claim_id}")
        evidence = [_relative(value, f"{claim_id}.evidence") for value in _text_list(entry.get("result_evidence"), f"{claim_id}.result_evidence")]
        validation = [_relative(value, f"{claim_id}.validation") for value in _text_list(entry.get("validation_evidence"), f"{claim_id}.validation_evidence")]
        for path in evidence + validation:
            if not (workspace / path).is_file():
                raise ScientificContractError(f"paper plan evidence missing: {path}")
        covered_approximations = set(_text_list(entry.get("approximation_ids"), f"{claim_id}.approximation_ids", empty=True))
        if not covered_approximations.issubset(approximation_ids):
            raise ScientificContractError(f"paper plan unknown approximation for {claim_id}")
        limitations = _text_list(
            entry.get("limitations"),
            f"{claim_id}.limitations",
        )
        disclosed_approximations.update(covered_approximations)
        figures = [
            _relative(value, f"{claim_id}.figure")
            for value in _text_list(entry.get("figures"), f"{claim_id}.figures", empty=True)
        ]
        for figure in figures:
            if not (workspace / figure).is_file():
                raise ScientificContractError(f"paper plan figure missing: {figure}")
        if strict:
            expected_figures = {
                spec["vector_path"] for problem in plan["problems"]
                for spec in problem.get("figure_specs", []) if claim_id in spec["claim_ids"]
            }
            if set(figures) != expected_figures:
                raise ScientificContractError(
                    f"paper plan figures for {claim_id} must equal accepted vector masters: {sorted(expected_figures)}"
                )
        seen.add(claim_id)
        normalized.append({
            "claim_id": claim_id,
            "problem_id": problem_id,
            "section_id": _id(entry.get("section_id"), f"{claim_id}.section_id"),
            "interpretation_and_assumptions": _text(entry.get("interpretation_and_assumptions"), f"{claim_id}.interpretation_and_assumptions"),
            "model_or_equations": _text_list(entry.get("model_or_equations"), f"{claim_id}.model_or_equations"),
            "algorithm_and_stopping": _text(entry.get("algorithm_and_stopping"), f"{claim_id}.algorithm_and_stopping"),
            "result_evidence": evidence,
            "validation_evidence": validation,
            "sensitivity_or_robustness": _text(entry.get("sensitivity_or_robustness"), f"{claim_id}.sensitivity_or_robustness"),
            "approximation_ids": sorted(covered_approximations),
            "limitations": limitations,
            "figures": figures,
            "citations_needed": _text_list(entry.get("citations_needed"), f"{claim_id}.citations_needed", empty=True),
        })
    if seen != set(expected_claims):
        raise ScientificContractError(f"paper plan missing claims: {sorted(set(expected_claims) - seen)}")
    if disclosed_approximations != expected_approximations:
        raise ScientificContractError(
            f"paper plan missing approximation limitations: {sorted(expected_approximations - disclosed_approximations)}"
        )
    return {
        "schema_version": 1,
        "plan_version": plan["plan_version"],
        "recommended_page_range": page_range,
        "coverage": normalized,
    }


def paper_source_errors(
    workspace: Path, *, legacy_visual: bool = True, strict: bool = False,
) -> list[str]:
    """Check explicit LaTeX references and obvious page-padding constructs."""
    paper = workspace / "paper"
    if strict:
        if not (paper / "main.tex").is_file():
            return []
        sources, source_errors = _reachable_paper_sources(paper, [paper / "main.tex"])
        text = "\n".join(sources.values())
        errors = [f"paper_sources: {error}" for error in source_errors]
    else:
        paths = sorted(paper.rglob("*.tex")) if paper.is_dir() else []
        text = "\n".join(
            re.sub(r"(?<!\\)%.*$", "", line)
            for path in paths
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        errors = []
    bibliography_keys = re.findall(
        r"\\bibitem(?:\s*\[[^\]]*\])?\s*\{([^{}\s]+)\}", text
    )
    if bibliography_keys or strict:
        duplicates = sorted(
            key for key in set(bibliography_keys) if bibliography_keys.count(key) > 1
        )
        if duplicates:
            errors.append(f"paper_references: duplicate bibitem keys: {duplicates}")
        cited: set[str] = set()
        for group in re.findall(
            r"\\(?:cite|citep|citet|autocite|parencite|textcite)"
            r"(?:\s*\[[^\]]*\]){0,2}\s*\{([^{}]+)\}",
            text,
        ):
            cited.update(key.strip() for key in group.split(",") if key.strip())
        available = set(bibliography_keys)
        uncited = sorted(available - cited)
        unknown = sorted(cited - available)
        if uncited:
            errors.append(f"paper_references: uncited bibitem keys: {uncited}")
        if unknown:
            errors.append(f"paper_references: citations without bibitems: {unknown}")
        if len(available) <= 5 and re.search(
            r"\\(?:newpage|clearpage)\s*"
            r"(?:\\referencescn\b|\\input\s*\{references\}|\\begin\s*\{thebibliography\})",
            text,
        ):
            errors.append(
                "paper_layout: short references must not be forced onto a separate page"
            )
    if len(re.findall(r"\\tableofcontents\b", text)) > 1:
        errors.append("paper_layout: table of contents appears more than once")
    if re.search(
        r"\\(?:newpage|clearpage)\s*\\(?:newpage|clearpage)\b", text
    ):
        errors.append("paper_layout: consecutive forced page breaks")

    if legacy_visual and (paper / "main.pdf").is_file() and (paper / "main.log").is_file():
        log_text = (paper / "main.log").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"Output written on .*?\((\d+) pages?(?:,\s*\d+ bytes)?\)", log_text)
        if not match:
            errors.append("paper_visual: main.log does not report the compiled page count")
        else:
            from PIL import Image

            for number in range(1, int(match.group(1)) + 1):
                for suffix in ("", "-gray"):
                    relative = f"paper/rendered_pages/page-{number:02d}{suffix}.png"
                    try:
                        with Image.open(workspace / relative) as image:
                            image.load()
                            size, extrema = image.size, image.convert("L").getextrema()
                        if min(size) < 800:
                            errors.append(f"paper_visual: rendered page is below readable resolution: {relative} ({size[0]}x{size[1]})")
                        if extrema[0] == extrema[1]:
                            errors.append(f"paper_visual: rendered page is blank: {relative}")
                    except (FileNotFoundError, OSError, ValueError) as exc:
                        errors.append(f"paper_visual: missing or unreadable rendered page: {relative}: {exc}")

    return errors


def _reachable_paper_sources(
    paper: Path, pending: list[Path],
) -> tuple[dict[Path, str], list[str]]:
    sources: dict[Path, str] = {}
    errors: list[str] = []
    while pending:
        path = pending.pop().resolve()
        if path in sources:
            continue
        try:
            path.relative_to(paper.resolve())
            text = path.read_text(encoding="utf-8")
            text = re.sub(r"(?m)(?<!\\)%.*$", "", text) if path.suffix == ".tex" else re.sub(r"(?m)//.*$", "", text)
            sources[path] = text
            includes = re.findall(r"\\(?:input|include)\s*\{([^{}]+)\}", text)
            includes += re.findall(r'#(?:include|import)\s+"([^"]+)"', text)
            for name in includes:
                candidates = [paper / name, path.parent / name]
                if not Path(name).suffix:
                    candidates = [candidate.with_suffix(path.suffix) for candidate in candidates]
                child = next((candidate for candidate in candidates if candidate.is_file()), None)
                if child is None:
                    raise ValueError(f"unresolved literal paper source: {name}")
                pending.append(child)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    return sources, errors


def _paper_figure_errors(workspace: Path, paper_plan: dict[str, Any], sections: list[str]) -> list[str]:
    paper = workspace / "paper"
    master = paper / ("main.tex" if (paper / "main.tex").is_file() else "main.typ")
    pending = [master] if master.is_file() else [workspace / section for section in sections]
    sources, source_errors = _reachable_paper_sources(paper, pending)
    errors = [f"paper_figures: {error}" for error in source_errors]
    search_roots = [paper]
    for text in sources.values():
        for group in re.findall(r"\\graphicspath\s*\{((?:\s*\{[^{}]+\})+\s*)\}", text):
            search_roots.extend(paper / name for name in re.findall(r"\{([^{}]+)\}", group))
    planned = {(workspace / figure).resolve() for row in paper_plan["coverage"] for figure in row["figures"]}
    problem_roots = [(workspace / "figures" / row["problem_id"]).resolve() for row in paper_plan["coverage"]]
    used: set[Path] = set()
    for path, text in sources.items():
        names = re.findall(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}", text)
        names += re.findall(r'\bimage\(\s*"([^"]+)"', text)
        for name in names:
            candidates = [root / name for root in [*search_roots, path.parent]]
            if not Path(name).suffix:
                candidates = [candidate.with_suffix(ext) for candidate in candidates for ext in (".pdf", ".png", ".jpg", ".jpeg")]
            figure = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
            if figure is None:
                errors.append(f"paper_figures: unresolved literal figure: {name}")
                continue
            used.add(figure)
            if figure not in planned and (
                not figure.is_relative_to((workspace / "figures").resolve())
                or any(figure.is_relative_to(root) for root in problem_roots)
            ):
                errors.append(f"paper_figures: unplanned scientific figure or paper replacement: {name}")
    for figure in sorted(planned - used):
        errors.append(f"paper_figures: planned frozen figure is not included: {figure.relative_to(workspace.resolve()).as_posix()}")
    return errors


def validate_paper_manifest(
    workspace: Path, paper_plan: dict[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    path = workspace / "paper" / "paper_manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ScientificContractError(f"paper_manifest.json missing or invalid: {exc}") from exc
    item = _object(raw, "paper_manifest")
    if item.get("schema_version") != 1 or item.get("plan_version") != paper_plan.get("plan_version"):
        raise ScientificContractError("paper manifest schema/plan version mismatch")
    expected = {entry["claim_id"] for entry in paper_plan["coverage"]}
    seen: set[str] = set()
    for index, raw_entry in enumerate(_list(item.get("coverage"), "paper_manifest.coverage")):
        entry = _object(raw_entry, f"paper_manifest.coverage[{index}]")
        claim_id = _text(entry.get("claim_id"), f"manifest[{index}].claim_id")
        if claim_id not in expected or claim_id in seen:
            raise ScientificContractError(f"paper manifest unknown or duplicate claim: {claim_id}")
        section = _relative(entry.get("section_file"), f"{claim_id}.section_file")
        section_path = workspace / section
        if not section_path.is_file() or not section.startswith("paper/"):
            raise ScientificContractError(f"paper manifest section missing: {section}")
        source = section_path.read_text(encoding="utf-8", errors="replace")
        anchors = _object(entry.get("anchors"), f"{claim_id}.anchors")
        expected_anchor_names = {
            "model", "algorithm", "result", "validation", "conclusion", "limitation"
        }
        if set(anchors) != expected_anchor_names:
            raise ScientificContractError(
                f"paper manifest anchor keys mismatch: {claim_id}"
            )
        anchor_values: dict[str, str] = {}
        for name in expected_anchor_names:
            anchor = _text(anchors.get(name), f"{claim_id}.anchors.{name}")
            if source.count(anchor) != 1:
                raise ScientificContractError(
                    f"paper manifest anchor must occur exactly once: {claim_id}.{name}"
                )
            anchor_values[name] = anchor
        if len(set(anchor_values.values())) != len(anchor_values):
            raise ScientificContractError(
                f"paper manifest anchors must be distinct: {claim_id}"
            )
        limitation = anchor_values["limitation"]
        for name, anchor in anchor_values.items():
            if name != "limitation" and (limitation in anchor or anchor in limitation):
                raise ScientificContractError(
                    f"paper manifest limitation overlaps {name} anchor: {claim_id}"
                )
        figures = _text_list(entry.get("figures"), f"{claim_id}.figures", empty=True)
        if strict:
            planned = next(row["figures"] for row in paper_plan["coverage"] if row["claim_id"] == claim_id)
            if set(figures) != set(planned):
                raise ScientificContractError(f"paper manifest scientific figures differ from frozen plan: {claim_id}")
        for figure in figures:
            figure_path = _relative(figure, f"{claim_id}.figure")
            if not (workspace / figure_path).is_file():
                raise ScientificContractError(f"paper manifest figure missing: {figure_path}")
        seen.add(claim_id)
    if seen != expected:
        raise ScientificContractError(f"paper manifest missing claims: {sorted(expected - seen)}")
    if strict:
        paper = workspace / "paper"
        master = paper / ("main.tex" if (paper / "main.tex").is_file() else "main.typ")
        sources, source_errors = _reachable_paper_sources(paper, [master])
        errors = [f"paper_sources: {error}" for error in source_errors]
        for row in item["coverage"]:
            path = (workspace / row["section_file"]).resolve()
            if path not in sources:
                errors.append(f"paper_manifest: claim section is not reachable from master: {row['section_file']}")
            elif any(sources[path].count(anchor) != 1 for anchor in row["anchors"].values()):
                errors.append(f"paper_manifest: anchors must occur in uncommented reachable source: {row['claim_id']}")
        errors.extend(_paper_figure_errors(workspace, paper_plan, [row["section_file"] for row in item["coverage"]]))
        if errors:
            raise ScientificContractError("; ".join(errors))
    return item

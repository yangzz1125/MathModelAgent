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


class ContractError(ValueError):
    """A generated workflow artifact violates the local contract."""


def relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("path must be a non-empty string")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError(f"unsafe relative path: {value}")
    return path.as_posix()


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
            try:
                science = validate_problem_science(item, problem_id)
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
    phases = [
        {
            "id": "planning",
            "label": "赛题分析与建模",
            "status": "running",
            "attempts": 1,
            "started_at": None,
            "completed_at": None,
            "last_error": "",
        }
    ]
    if contract_version == 2:
        phases.append(
            {
                "id": "plan_audit",
                "label": "独立计划审查",
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
        "plan_version": 1,
        "current": "planning",
        "mode": "run",
        "profiles": {"planner": planner, "worker": worker},
        "phases": phases,
        "frozen": {},
    }


def expand_problem_phases(workflow: dict[str, Any], plan: dict[str, Any]) -> None:
    prefix_count = 2 if workflow.get("contract_version") == 2 else 1
    prefix = workflow["phases"][:prefix_count]
    final_phases = V2_FINAL_PHASES if workflow.get("contract_version") == 2 else FINAL_PHASES
    workflow["phases"] = prefix + [
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
    ] + [
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
            if path.is_file():
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
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if relative.parts[0].startswith(".pi") or relative.as_posix() == "project.json":
            continue
        hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def stage_scope_errors(
    workspace: Path, baseline: dict[str, str], stage: str
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
    if stage == "planning":
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
        path for path in changed if not any(path.startswith(prefix) for prefix in allowed)
    )
    return [f"artifact_changed: stage wrote outside its boundary: {path}" for path in disallowed]


def planning_prompt(*, problem_file: str, competition: str, language: str, paper_engine: str, notes: str) -> str:
    return f"""You are the planning stage only. Fully read $MATHMODELAGENT_ROOT/skills/2analysis-modeling/SKILL.md, $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md, {problem_file}, input_manifest.json, and relevant files under input/.

Use competition family {competition}, paper language {language}, and paper engine {paper_engine}. Write reports/ANALYSIS_MODELING_REPORT.md and execution_plan.json. Do not write solver code, run the full solution, draw diagrams, or write the paper. Stop after both planning artifacts exist.

execution_plan.json must use schema_version 2 and plan_version 1 with an ordered non-empty problems array. Each problem requires the existing id, label, depends_on, method, inputs, outputs, validation, and runtime_limit_seconds fields plus: requested_outputs, interpretation, assumptions, claims, approximations, failure_semantics, independent_validation, and figure_specs.

Every claim needs a stable id, a generic claim type, its exact statement, required evidence, and an acceptance criterion with an optional numeric tolerance. Use these exact nested shapes: assumptions=[{{id,statement,rationale,validation}}]; claims=[{{id,type,statement,evidence_required,acceptance:{{criterion,tolerance}}}}]; approximations=[{{id,original_quantity,surrogate_quantity,justification,error_or_equivalence_check}}]; failure_semantics=[{{condition,classification,action}}]; independent_validation=[{{id,method,independent_from,claims}}]. Every claim must be covered by an independent_validation entry that states how it differs from the primary method. assumptions and approximations may be empty arrays, but failure_semantics, claims, and independent_validation may not. Any surrogate, discretization, dimensional reduction, heuristic, or proxy must be declared with a justification and an error/equivalence check. failure_semantics must keep numerical/data/solver failures separate from mathematical infeasibility and domain events.

Inputs may use input/, input_manifest.json, reports/ANALYSIS_MODELING_REPORT.md, and declared earlier dependencies only. Outputs must stay under code/<id>/, results/<id>/, figures/<id>/, or reports/<id>_RESULTS.md. Every problem must declare code, result, and report outputs.

Read $MATHMODELAGENT_ROOT/pi/skills/mathmodel-figure-quality/references/figure-reference-catalog.json before planning figures. `figure_specs` is `[]` when no scientific figure is warranted. Otherwise each entry must use exactly: id, claim_ids, purpose, plot_family, reference_id, panels, primary_encoding, secondary_encoding, required_annotations, final_width, vector_path, preview_path, generator_path, and data_paths. Copy plot_family exactly from the selected catalog reference. Use 1--3 panels and final_width `single_column`, `double_column`, or `full`; primary_encoding is one of `position`, `length`, `color`, `area`, or `angle`. Declare every vector master, PNG preview, generating script, and generated data path in outputs. Each figure must state which claim, reader takeaway, and decision it supports. Do not require a figure merely for decoration or page count. Reference previews are layout inspiration only and are never evidence. State correctness conditions, complexity, stopping rule, and expected runtime before choosing a method.

User notes: {notes or 'None'}"""


def problem_prompt(problem: dict[str, Any]) -> str:
    payload = json.dumps(problem, ensure_ascii=False, indent=2)
    return f"""Execute exactly one modeling subproblem and then stop. Fully read $MATHMODELAGENT_ROOT/skills/3coding-visual/SKILL.md, $MATHMODELAGENT_ROOT/pi/skills/mathmodel-figure-quality/SKILL.md, its references/figure-routing.md, reports/ANALYSIS_MODELING_REPORT.md, and this task contract:

{payload}

Do not start any later problem. Write implementation only under code/{problem['id']}/, numerical artifacts under results/{problem['id']}/, data figures under figures/{problem['id']}/, and the report reports/{problem['id']}_RESULTS.md. Treat input/ and every earlier problem directory as read-only.

First implement and time the smallest representative computation. Record smoke_runtime_seconds and estimated_runtime_seconds before the full run. If the estimate exceeds {problem['runtime_limit_seconds']} seconds, change the algorithm before running it. Use one fixed mathematical predicate for search and acceptance; extra precision checks may test sensitivity but may not redefine feasibility. Keep numerical errors distinct from mathematical infeasibility. Implement every declared claim and independent validation; do not silently introduce an approximation absent from the contract.

For every scientific data figure, follow its Planner-owned `figure_specs` entry. Open the selected `reference_id` preview from $MATHMODELAGENT_ROOT/pi/skills/mathmodel-figure-quality/references/figure-reference-catalog.json and preserve only its explanatory structure, hierarchy and encoding logic. Do not substitute another reference or change the planned claims, purpose, family, paths or panels. Ordinary plots must use SciencePlots + the official Seaborn/Matplotlib API; specialized template layouts are allowed only when selected by the plan. Replace all reference/template simulation with current workspace data. Save the generator, source data, vector master and PNG preview at the exact planned paths. Render and inspect at final paper size.

Write results/{problem['id']}/result.json with problem_id, status='candidate', and non-empty metrics entries containing name, finite numeric value, unit, and description. Write results/{problem['id']}/verification.json with schema_version=2, status='candidate', smoke_runtime_seconds, estimated_runtime_seconds, actual_runtime_seconds, non-empty checks, a `figures` list (empty only when figure_specs is empty), and exactly one claim_evidence entry per declared claim. Every figures entry must match the schema in the figure-quality Skill, include the exact planned `spec_id` and `reference_id`, and point to real data, generator, vector master, PNG preview, supporting claims, style stack, language, and completed checks. Each claim_evidence entry needs claim_id, status='supported', method, independent boolean, and non-empty evidence_paths inside the current problem boundary. You are proposing a candidate, not accepting your own work. Run the checks and stop."""


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


def plan_audit_prompt() -> str:
    contract = _review_json_contract("plan", "")
    return f"""Act as an independent scientific plan auditor. Re-read the original problem statement, relevant attachments, $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md, reports/ANALYSIS_MODELING_REPORT.md, and execution_plan.json. Do not trust the planner summary, edit files, run expensive computation, or begin implementation.

Check statement coverage, dependency correctness, assumptions, every approximation or surrogate, claim-specific evidence obligations, failure semantics, independent validation, optimality/event/convergence claims, runtime feasibility, and every figure_specs entry. Reject figure plans whose reference family does not fit the claim, whose panels do not share a scientific context, whose annotations or encodings cannot expose the stated takeaway, or whose source-data path is not real problem evidence. Reject plans that can pass by self-reporting, conflate solver failure with a domain event, use an undeclared proxy, or provide validation equivalent to the primary method.

Return only strict JSON matching this shape, with null problem_id. No prose or Markdown fences:
{contract}"""


def plan_revision_prompt(audit: dict[str, Any]) -> str:
    evidence = json.dumps(audit, ensure_ascii=False, indent=2)
    return f"""The independent plan audit rejected execution_plan.json. Re-read the original inputs and revise reports/ANALYSIS_MODELING_REPORT.md plus the full schema-v2 execution_plan.json in place. Preserve problem IDs and valid dependency order. Resolve every issue below without writing code or starting execution. Stop after validating both planning artifacts.

Audit evidence:
{evidence}"""


def scientific_review_prompt(problem: dict[str, Any]) -> str:
    contract = _review_json_contract("scientific", problem["id"])
    return f"""Act as the independent scientific acceptance reviewer for {problem['id']}. Re-read the original problem and relevant attachments, $MATHMODELAGENT_ROOT/skills/_references/math_modeling_norms.md, execution_plan.json, accepted dependency artifacts, and this problem's code, result, verification evidence, and report. Do not trust candidate status, planner claims, or worker prose. Do not edit/create files or run expensive computations.

Check that the candidate answers the exact requested output, implements the declared method, exposes every approximation, keeps failure semantics correct, supports optimality/event/feasibility claims with appropriate evidence, and uses genuinely independent validation. Also inspect every `verification.json.figures` entry, its matching Planner `figure_specs` entry, selected reference preview, real source data, generator, vector and rendered preview. The produced figure must preserve the reference's useful explanatory structure without copying its data. Axes, units, scales, uncertainty and sample size must not mislead; bars require a defensible baseline; heatmaps require a meaningful colormap/center; plotted values must agree with frozen candidate evidence; labels and legends must be readable.

Apply this 100-point figure rubric internally to each figure: scientific purpose 20, claim/plot-family fit 20, data and scale integrity 20, visual hierarchy and information density 15, direct annotations 10, color plus grayscale readability 10, and final paper-size readability 5. Any semantic/data/provenance defect is a hard reject regardless of score. Reject scores below 85 with concrete required repairs; 70--84 is a visual-quality repair, not acceptance. Do not add rubric fields to the strict JSON response. A runnable program, attractive plot, and internally consistent numbers are insufficient by themselves.

Return only strict JSON matching this shape. No prose or Markdown fences:
{contract}"""


def scientific_repair_prompt(problem: dict[str, Any], review: dict[str, Any]) -> str:
    evidence = json.dumps(review, ensure_ascii=False, indent=2)
    return f"""Scientific review rejected candidate {problem['id']} with issue class {review['issue_class']}. Repair only code/{problem['id']}/, results/{problem['id']}/, figures/{problem['id']}/, and reports/{problem['id']}_RESULTS.md. Do not alter the scientific contract, input, accepted dependencies, or later problems. Resolve every required repair, rerun the primary and independent checks, regenerate candidate result/verification artifacts, and stop.

Review evidence:
{evidence}"""


def method_replan_prompt(problem_id: str, review: dict[str, Any], downstream_ids: list[str]) -> str:
    evidence = json.dumps(review, ensure_ascii=False, indent=2)
    return f"""Scientific review found a method-level or unresolved ambiguity in {problem_id}. Re-read the original inputs and current execution_plan.json. Write execution_plan.revision.json only; do not edit the active plan, code, results, reports, or accepted upstream artifacts.

The revision must contain schema_version=1, base_plan_version equal to the current plan_version, and revised_problems containing complete schema-v2 contracts for exactly these IDs in order: {downstream_ids}. For ambiguity, evaluate all defensible interpretations; include multiple branches when affordable, otherwise adopt the most conservative interpretation that does not overstate conclusions and record it explicitly. Resolve every review issue.

Review evidence:
{evidence}"""


def paper_planning_prompt(plan_version: int) -> str:
    return f"""Act as the scientific paper content planner only. Read execution_plan.json plan_version {plan_version}, every accepted scientific review, frozen result/report/figure artifact, each accepted `verification.json.figures` provenance entry, and $MATHMODELAGENT_ROOT/skills/5writing/SKILL.md. Do not write paper source or modify accepted artifacts.

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


def final_stage_prompt(stage: str, *, competition: str, language: str, paper_engine: str) -> str:
    if stage == "diagram":
        return """Fully read $MATHMODELAGENT_ROOT/skills/4drawio/SKILL.md. Read only accepted reports/results and paper_plan.json. Create only conceptual diagrams that add scientific information and are explicitly useful to the paper; do not generate generic roadmaps, decorative architecture diagrams, or duplicate a data figure. Always write reports/DRAWIO_REPORT.md; if no conceptual diagram is needed, explain that there. Do not modify code or accepted results. Stop after this stage."""
    if stage == "writing":
        return f"""Fully read $MATHMODELAGENT_ROOT/skills/5writing/SKILL.md, paper_plan.json, reports/PAPER_PLAN.md, execution_plan.json, all accepted scientific reviews, frozen evidence, and accepted `verification.json.figures` entries. Write the complete {language} {competition} paper using {paper_engine}. Cover every paper-plan claim with its model/equations, algorithm and stopping rule, result evidence, independent validation, sensitivity/robustness, conclusion, and disclosed limitations. Use only accepted frozen evidence for numerical claims; do not recompute, invent values, redraw data, pad length, or omit evidence because a section is short.

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
        return """Fully read $MATHMODELAGENT_ROOT/skills/6verity/SKILL.md, paper_plan.json, paper/paper_manifest.json, and accepted `verification.json.figures` entries. This is Document Verification, not scientific re-acceptance. Confirm every accepted claim is substantively covered, all six manifest anchors per claim are unique and semantically truthful, frozen evidence and paper numbers agree, required models/results/independent validations/limitations are present, every listed reference is cited in the body and every citation resolves, compilation succeeds, and every PDF page is visually readable. For every paper figure, confirm the embedded source is the accepted frozen vector master, labels match the paper language, units/scales/legend remain readable at final size, grayscale distinctions survive, and no element overlaps or clips. Confirm there is exactly one contents sequence and that short references are not isolated by unnecessary forced page breaks. Short page count alone is only a warning; missing required scientific content is a hard failure. Do not modify accepted code/results or rewrite/redraw the paper. Temporary isolated checks may use _tmp/, but remove _tmp/ before stopping. If any hard check fails, write a `Required repairs` section with one concrete item per defect, including exact source file, physical PDF page, and affected text/element when available. Write reports/VERIFY_REPORT.md with an unambiguous standalone PASS conclusion only if every hard check passes, then stop."""
    raise ValueError(f"unknown final stage: {stage}")

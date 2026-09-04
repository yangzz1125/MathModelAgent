"""Tests for the Pi bridge's deterministic local behavior."""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile

import pi.bridge as bridge
from pi.bridge import (
    RPC_STREAM_LIMIT_BYTES,
    TASKS,
    TaskRuntime,
    _document_stack_errors,
    _initialize_project,
    _phase_statuses,
    _safe_file,
    _task_model_config,
    _upload_path,
    _verification_passed,
    _visible_files,
    _visible_text,
)
from pi.tests.validate_simple_bakery import validate as validate_simple_bakery


from pi.scientific_review import (
    ScientificContractError,
    acceptance_chain_errors,
    candidate_errors,
    merge_plan_revision,
    paper_plan_frozen_errors,
    paper_source_errors,
    parse_method_review,
    parse_review,
    plan_completeness_receipt,
    validate_paper_manifest,
    validate_paper_plan,
)
from pi.staged_workflow import (
    ContractError,
    artifact_hashes,
    canonical_hash,
    expand_problem_phases,
    final_stage_prompt,
    frozen_errors,
    initial_workflow,
    method_version_dir,
    plan_revision_prompt,
    result_errors,
    spike_budget,
    stage_scope_errors,
    validate_execution_plan,
    validate_method_card,
    validate_problem_inventory,
    validate_spike_report,
    workspace_hashes,
)


class ScientificContractsTest(unittest.TestCase):
    def _science(self, problem_id: str = "q1") -> dict[str, object]:
        claim_id = f"{problem_id}.objective"
        return {
            "requested_outputs": ["optimal decision and objective"],
            "interpretation": "Use the quantities requested by the statement without surrogate replacement.",
            "assumptions": [],
            "claims": [{
                "id": claim_id,
                "type": "optimality",
                "statement": "The reported feasible decision is globally optimal.",
                "evidence_required": ["feasibility residuals", "independent vertex enumeration"],
                "acceptance": {"criterion": "No feasible vertex has larger objective.", "tolerance": 1e-9},
            }],
            "approximations": [],
            "failure_semantics": [{
                "condition": "solver raises an exception",
                "classification": "numerical_error",
                "action": "repair the solver; do not classify the model as infeasible",
            }],
            "independent_validation": [{
                "id": f"{problem_id}.vertex_check",
                "method": "Enumerate all polygon vertices independently.",
                "independent_from": "closed-form active-set candidate",
                "claims": [claim_id],
            }],
        }

    def test_schema_v2_requires_complete_scientific_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "input").mkdir()
            (workspace / "input" / "problem.md").write_text("problem")
            (workspace / "reports").mkdir()
            problem = {
                "id": "q1", "label": "Problem 1", "depends_on": [],
                "method": "enumerate", "inputs": ["input/problem.md"],
                "outputs": ["code/q1/solve.py", "results/q1/result.json", "reports/q1_RESULTS.md"],
                "validation": ["oracle"], "runtime_limit_seconds": 60,
                **self._science(),
            }
            path = workspace / "execution_plan.json"
            path.write_text(json.dumps({"schema_version": 2, "plan_version": 1, "problems": [problem]}))
            plan = validate_execution_plan(workspace)
            self.assertEqual(plan["schema_version"], 2)
            self.assertEqual(plan["problems"][0]["claims"][0]["id"], "q1.objective")
            self.assertEqual(plan["problems"][0]["figure_specs"], [])

            problem["outputs"].append("figures/q1/value.png")
            path.write_text(json.dumps({"schema_version": 2, "plan_version": 1, "problems": [problem]}))
            with self.assertRaisesRegex(ContractError, "figure_specs is required"):
                validate_execution_plan(workspace)
            problem["outputs"].pop()

            problem.pop("failure_semantics")
            path.write_text(json.dumps({"schema_version": 2, "plan_version": 1, "problems": [problem]}))
            with self.assertRaisesRegex(ContractError, "failure_semantics"):
                validate_execution_plan(workspace)

    def test_strict_review_accept_and_reject(self) -> None:
        accepted = {
            "schema_version": 1, "review_type": "scientific", "problem_id": "q1",
            "verdict": "accept", "statement_alignment": "pass", "method_validity": "pass",
            "implementation_fidelity": "pass", "evidence_sufficiency": "pass",
            "issue_class": "none", "issues": [], "required_repairs": [],
        }
        self.assertEqual(parse_review(json.dumps(accepted), review_type="scientific", problem_id="q1")["verdict"], "accept")
        with self.assertRaisesRegex(ScientificContractError, "strict JSON"):
            parse_review("Looks good " + json.dumps(accepted), review_type="scientific", problem_id="q1")
        accepted["verdict"] = "reject"
        with self.assertRaisesRegex(ScientificContractError, "requires issue_class"):
            parse_review(json.dumps(accepted), review_type="scientific", problem_id="q1")

    def test_acceptance_chain_requires_all_strict_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "reports").mkdir()
            plan_review = {
                "schema_version": 1, "review_type": "plan", "problem_id": None,
                "verdict": "accept", "statement_alignment": "pass", "method_validity": "pass",
                "implementation_fidelity": "pass", "evidence_sufficiency": "pass",
                "issue_class": "none", "issues": [], "required_repairs": [],
            }
            science_review = {**plan_review, "review_type": "scientific", "problem_id": "q1"}
            (workspace / "reports" / "PLAN_AUDIT.json").write_text(json.dumps(plan_review))
            (workspace / "reports" / "q1_SCIENTIFIC_REVIEW.json").write_text(json.dumps(science_review))
            plan = {"problems": [{"id": "q1"}]}
            self.assertEqual(acceptance_chain_errors(workspace, plan), [])
            (workspace / "reports" / "q1_SCIENTIFIC_REVIEW.json").write_text("{}")
            self.assertTrue(acceptance_chain_errors(workspace, plan))

    def test_candidate_never_self_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for name in ("code/q1", "results/q1", "reports"):
                (workspace / name).mkdir(parents=True, exist_ok=True)
            evidence = workspace / "results" / "q1" / "evidence.json"
            evidence.write_text("{}")
            (workspace / "results" / "q1" / "result.json").write_text(json.dumps({
                "problem_id": "q1", "status": "pass", "metrics": [{
                    "name": "objective", "value": 1, "unit": "", "description": "candidate"
                }]
            }))
            (workspace / "results" / "q1" / "verification.json").write_text(json.dumps({
                "schema_version": 2, "status": "candidate",
                "smoke_runtime_seconds": 0.01, "estimated_runtime_seconds": 0.1,
                "actual_runtime_seconds": 0.1, "checks": ["vertex check"],
                "figures": [],
                "claim_evidence": [{
                    "claim_id": "q1.objective", "status": "supported", "independent": True,
                    "method": "vertex check", "evidence_paths": ["results/q1/evidence.json"]
                }]
            }))
            problem = {"id": "q1", "claims": self._science()["claims"]}
            self.assertIn(
                "candidate_protocol: worker may only report status=candidate",
                candidate_errors(workspace, problem),
            )
            result = json.loads((workspace / "results" / "q1" / "result.json").read_text())
            result["status"] = "candidate"
            (workspace / "results" / "q1" / "result.json").write_text(json.dumps(result))
            self.assertEqual(candidate_errors(workspace, problem), [])

    def test_plan_revision_replaces_only_current_and_downstream(self) -> None:
        base = {
            "schema_version": 2, "plan_version": 1,
            "problems": [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}],
        }
        revision = {
            "schema_version": 1, "base_plan_version": 1,
            "revised_problems": [{"id": "q2", "method": "new"}, {"id": "q3", "method": "new"}],
        }
        merged = merge_plan_revision(base, revision, "q2")
        self.assertEqual(merged["plan_version"], 2)
        self.assertEqual(merged["problems"][0], {"id": "q1"})
        revision["revised_problems"] = [{"id": "q3"}]
        with self.assertRaisesRegex(ScientificContractError, "current and all"):
            merge_plan_revision(base, revision, "q2")

    def test_manifest_rejects_renamed_figures_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "paper").mkdir()
            (workspace / "paper" / "q1.tex").write_text(
                "MODEL ALGORITHM RESULT VALIDATION CONCLUSION LIMITATION"
            )
            paper_plan = {
                "plan_version": 1,
                "coverage": [{"claim_id": "q1.claim", "approximation_ids": []}],
            }
            manifest = {
                "schema_version": 1, "plan_version": 1,
                "coverage": [{
                    "claim_id": "q1.claim", "section_file": "paper/q1.tex",
                    "anchors": {
                        "model": "MODEL", "algorithm": "ALGORITHM", "result": "RESULT",
                        "validation": "VALIDATION", "conclusion": "CONCLUSION",
                        "limitation": "LIMITATION",
                    },
                    "figure_paths": [],
                }],
            }
            (workspace / "paper" / "paper_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ScientificContractError, "figures must be a list"):
                validate_paper_manifest(workspace, paper_plan)

    def test_paper_plan_and_manifest_cover_every_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "results" / "q1").mkdir(parents=True)
            (workspace / "results" / "q1" / "result.json").write_text("{}")
            (workspace / "results" / "q1" / "evidence.json").write_text("{}")
            (workspace / "figures" / "q1").mkdir(parents=True)
            (workspace / "figures" / "q1" / "chart.pdf").write_bytes(b"%PDF-test")
            plan = {
                "schema_version": 2, "plan_version": 1,
                "problems": [{"id": "q1", "claims": self._science()["claims"], "approximations": []}],
            }
            paper_plan = {
                "schema_version": 1, "plan_version": 1, "recommended_page_range": [8, 14],
                "coverage": [{
                    "claim_id": "q1.objective", "problem_id": "q1", "section_id": "q1",
                    "interpretation_and_assumptions": "Explain the continuous production model.",
                    "model_or_equations": ["objective", "constraints"],
                    "algorithm_and_stopping": "Enumerate all vertices and stop after all pairs.",
                    "result_evidence": ["results/q1/result.json"],
                    "validation_evidence": ["results/q1/evidence.json"],
                    "sensitivity_or_robustness": "Check active constraints under capacity perturbation.",
                    "approximation_ids": [],
                    "limitations": ["Only valid for the stated capacity."],
                    "figures": ["figures/q1/chart.pdf"], "citations_needed": [],
                }],
            }
            (workspace / "paper_plan.json").write_text(json.dumps(paper_plan))
            normalized = validate_paper_plan(workspace, plan)
            paper_plan_without_limit = json.loads(json.dumps(paper_plan))
            paper_plan_without_limit["coverage"][0]["limitations"] = []
            (workspace / "paper_plan.json").write_text(
                json.dumps(paper_plan_without_limit)
            )
            with self.assertRaisesRegex(ScientificContractError, "limitations must be"):
                validate_paper_plan(workspace, plan)
            (workspace / "paper_plan.json").write_text(json.dumps(paper_plan))
            self.assertTrue(paper_plan_frozen_errors(normalized, {}))
            frozen = {"q1": {
                "results/q1/result.json": "hash", "results/q1/evidence.json": "hash",
                "figures/q1/chart.pdf": "hash",
            }}
            self.assertEqual(paper_plan_frozen_errors(normalized, frozen), [])
            (workspace / "paper").mkdir()
            section = workspace / "paper" / "q1.tex"
            section.write_text(
                "MODEL ANCHOR ALGORITHM ANCHOR RESULT ANCHOR VALIDATION ANCHOR "
                "CONCLUSION ANCHOR LIMITATION ANCHOR"
            )
            manifest = {
                "schema_version": 1, "plan_version": 1,
                "coverage": [{
                    "claim_id": "q1.objective", "section_file": "paper/q1.tex",
                    "anchors": {
                        "model": "MODEL ANCHOR", "algorithm": "ALGORITHM ANCHOR",
                        "result": "RESULT ANCHOR", "validation": "VALIDATION ANCHOR",
                        "conclusion": "CONCLUSION ANCHOR",
                        "limitation": "LIMITATION ANCHOR",
                    },
                    "figures": [],
                }],
            }
            (workspace / "paper" / "paper_manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(validate_paper_manifest(workspace, normalized)["plan_version"], 1)
            manifest["coverage"][0]["anchors"]["limitation"] = "MODEL"
            (workspace / "paper" / "paper_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ScientificContractError, "limitation overlaps model"):
                validate_paper_manifest(workspace, normalized)
            paper_plan["coverage"] = []
            (workspace / "paper_plan.json").write_text(json.dumps(paper_plan))
            with self.assertRaises(ScientificContractError):
                validate_paper_plan(workspace, plan)
    def test_paper_sources_require_used_references_and_natural_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            paper = workspace / "paper"
            sections = paper / "sections"
            sections.mkdir(parents=True)
            (paper / "references.tex").write_text(
                "\\begin{thebibliography}{9}\n"
                "\\bibitem{lp} Linear programming.\n"
                "\\bibitem{ip} Integer programming.\n"
                "\\end{thebibliography}\n"
            )
            body = sections / "body.tex"
            body.write_text("Primary method \\cite{lp}.\n")
            (paper / "main.tex").write_text(
                "\\tableofcontents\n\\input{sections/body}\n"
                "\\input{references}\n"
            )

            self.assertEqual(
                paper_source_errors(workspace),
                ["paper_references: uncited bibitem keys: ['ip']"],
            )
            body.write_text("Methods \\cite{lp,ip,missing}.\n")
            self.assertEqual(
                paper_source_errors(workspace),
                ["paper_references: citations without bibitems: ['missing']"],
            )
            body.write_text("Methods \\cite{lp,ip}.\n")
            self.assertEqual(paper_source_errors(workspace), [])

            (paper / "main.tex").write_text(
                "\\tableofcontents\n\\tableofcontents\n"
                "\\input{sections/body}\n\\newpage\n\\input{references}\n"
            )
            self.assertEqual(
                paper_source_errors(workspace),
                [
                    "paper_layout: short references must not be forced onto a separate page",
                    "paper_layout: table of contents appears more than once",
                ],
            )


class StagedWorkflowContractTest(unittest.TestCase):
    def _workspace(self, directory: str) -> Path:
        workspace = Path(directory)
        (workspace / "input").mkdir()
        (workspace / "input" / "problem.md").write_text("problem", encoding="utf-8")
        (workspace / "reports").mkdir()
        (workspace / "reports" / "ANALYSIS_MODELING_REPORT.md").write_text("analysis", encoding="utf-8")
        return workspace

    def _problem(self, problem_id: str = "q1", **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "id": problem_id,
            "label": f"Problem {problem_id}",
            "depends_on": [],
            "method": "enumerate vertices",
            "inputs": ["input/problem.md"],
            "outputs": [
                f"code/{problem_id}/solve.py",
                f"results/{problem_id}/result.json",
                f"reports/{problem_id}_RESULTS.md",
            ],
            "validation": ["independent vertex check"],
            "runtime_limit_seconds": 60,
        }
        value.update(changes)
        return value

    def test_plan_accepts_ordered_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            q2 = self._problem(
                "q2",
                depends_on=["q1"],
                inputs=["input/problem.md", "results/q1/result.json"],
            )
            (workspace / "execution_plan.json").write_text(
                json.dumps({"schema_version": 1, "problems": [self._problem(), q2]}),
                encoding="utf-8",
            )

            plan = validate_execution_plan(workspace)
            workflow = initial_workflow(
                {"model": "openai/gpt-5.6-sol", "thinking": "high"},
                {"model": "openai/gpt-5.6-luna", "thinking": "high"},
            )
            expand_problem_phases(workflow, plan)

            self.assertEqual([item["id"] for item in workflow["phases"]], [
                "planning", "problem:q1", "problem:q2", "diagram", "writing", "verify"
            ])

    def test_plan_rejects_duplicate_and_future_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            path = workspace / "execution_plan.json"
            path.write_text(
                json.dumps({"schema_version": 1, "problems": [self._problem(), self._problem()]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "duplicate"):
                validate_execution_plan(workspace)

            q1 = self._problem("q1", depends_on=["q2"])
            path.write_text(json.dumps({"schema_version": 1, "problems": [q1]}), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "future or unknown"):
                validate_execution_plan(workspace)

    def test_plan_rejects_traversal_and_cross_problem_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            path = workspace / "execution_plan.json"
            path.write_text(
                json.dumps({"schema_version": 1, "problems": [self._problem(inputs=["../secret"])]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "unsafe"):
                validate_execution_plan(workspace)

            path.write_text(
                json.dumps({"schema_version": 1, "problems": [self._problem(outputs=["results/q2/x.json"])]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractError, "artifact boundary"):
                validate_execution_plan(workspace)

    def test_stage_scope_rejects_future_and_input_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            baseline = workspace_hashes(workspace)
            (workspace / "results" / "q1").mkdir(parents=True)
            (workspace / "results" / "q1" / "result.json").write_text("{}")
            self.assertEqual(stage_scope_errors(workspace, baseline, "problem:q1"), [])

            verify_baseline = workspace_hashes(workspace)
            (workspace / "_tmp").mkdir()
            (workspace / "_tmp" / "check.txt").write_text("temporary")
            self.assertEqual(stage_scope_errors(workspace, verify_baseline, "verify"), [])

            (workspace / "results" / "q2").mkdir(parents=True)
            (workspace / "results" / "q2" / "future.json").write_text("{}")
            (workspace / "input" / "problem.md").write_text("changed")
            errors = stage_scope_errors(workspace, baseline, "problem:q1")
            self.assertTrue(any("results/q2/future.json" in error for error in errors))
            self.assertTrue(any("input/problem.md" in error for error in errors))

    def test_verify_prompt_keeps_compilation_outside_paper_boundary(self) -> None:
        prompt = final_stage_prompt(
            "verify", competition="MCM", language="English", paper_engine="LaTeX"
        )
        self.assertIn("Never run a compiler from paper/", prompt)
        self.assertIn("copy the complete paper source tree into _tmp/", prompt)

    def test_result_contract_and_frozen_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            problem = self._problem()
            code = workspace / "code" / "q1"
            results = workspace / "results" / "q1"
            code.mkdir(parents=True)
            results.mkdir(parents=True)
            (code / "solve.py").write_text("print(1)\n", encoding="utf-8")
            (workspace / "reports" / "q1_RESULTS.md").write_text("result", encoding="utf-8")
            (results / "result.json").write_text(json.dumps({
                "problem_id": "q1", "status": "pass", "metrics": [{
                    "name": "profit", "value": 2000, "unit": "yuan", "description": "maximum"
                }]
            }), encoding="utf-8")
            (results / "verification.json").write_text(json.dumps({
                "status": "pass", "smoke_runtime_seconds": 0.01,
                "estimated_runtime_seconds": 0.1, "actual_runtime_seconds": 0.1,
                "checks": ["vertex enumeration"]
            }), encoding="utf-8")

            self.assertEqual(result_errors(workspace, problem), [])
            frozen = {"q1": artifact_hashes(workspace, "q1")}
            self.assertEqual(frozen_errors(workspace, frozen), [])
            cache = code / "__pycache__"
            cache.mkdir()
            (cache / "solve.cpython-311.pyc").write_bytes(b"transient bytecode")
            self.assertEqual(frozen_errors(workspace, frozen), [])
            self.assertNotIn("code/q1/__pycache__/solve.cpython-311.pyc", workspace_hashes(workspace))
            (code / "solve.py").write_text("print(2)\n", encoding="utf-8")
            self.assertEqual(frozen_errors(workspace, frozen), [
                "artifact_changed: frozen artifacts for q1"
            ])


class IncrementalPlanningV3Test(unittest.IsolatedAsyncioTestCase):
    def _write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _inventory(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "problems": [{
                "id": "q1",
                "label": "Problem 1",
                "depends_on": [],
                "requested_outputs": [{
                    "id": "q1.output",
                    "statement": "optimal decision and objective",
                }],
                "input_paths": ["input/problem.md", "input_manifest.json"],
                "interpretation": "Use the stated continuous decision variables.",
                "ambiguities": [],
                "suggested_evidence_level": "B_bounded_numerical",
            }],
        }

    def _workspace(self, directory: str) -> Path:
        workspace = Path(directory)
        for name in ("input", "reports", "planning", "code", "results", "figures", "paper"):
            (workspace / name).mkdir(parents=True)
        (workspace / "input" / "problem.md").write_text("problem", encoding="utf-8")
        self._write(workspace / "input_manifest.json", {"problem_file": "input/problem.md"})
        self._write(
            workspace / "planning" / "inventory" / "v1" / "problem_inventory.json",
            self._inventory(),
        )
        (workspace / "reports" / "PROBLEM_INVENTORY_v1.md").write_text(
            "# Inventory\n", encoding="utf-8"
        )
        return workspace

    def _card(self, inventory: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "inventory_sha256": canonical_hash(inventory),
            "proposal_version": 1,
            "problem_id": "q1",
            "problem": {
                "id": "q1",
                "label": "Problem 1",
                "depends_on": [],
                "method": "Enumerate the finite feasible vertices.",
                "inputs": ["input/problem.md"],
                "outputs": [
                    "code/q1/solve.py",
                    "results/q1/result.json",
                    "reports/q1_RESULTS.md",
                ],
                "validation": ["Independent vertex enumeration"],
                "runtime_limit_seconds": 60,
                "requested_outputs": ["optimal decision and objective"],
                "interpretation": "Use the stated continuous decision variables.",
                "assumptions": [],
                "claims": [{
                    "id": "q1.objective",
                    "type": "optimality",
                    "statement": "The reported decision is optimal on the finite feasible polygon.",
                    "evidence_required": ["all feasible vertices"],
                    "acceptance": {
                        "criterion": "No enumerated feasible vertex is better.",
                        "tolerance": 1e-9,
                    },
                    "evidence_level": "B_bounded_numerical",
                    "requested_output_ids": ["q1.output"],
                    "limitations": ["Valid only on the finite stated feasible polygon."],
                }],
                "approximations": [],
                "failure_semantics": [{
                    "id": "q1.failure.enumeration",
                    "event_id": "q1.event.enumeration_failure",
                    "category": "numerical_failure",
                    "condition": "enumeration process fails",
                    "action": "repair the implementation",
                }],
                "independent_validation": [{
                    "id": "q1.check",
                    "method": "Enumerate intersections using a separate implementation.",
                    "independent_from": "primary active-set enumeration",
                    "claims": ["q1.objective"],
                }],
                "figure_specs": [],
            },
            "finite_domain": "All pairwise intersections of the stated linear constraints.",
            "witness_strategy": "Retain the best feasible vertex and its residuals.",
            "gap_or_tail_exclusion": "The finite vertex list exhausts the feasible polygon.",
            "cost_model": {
                "operation": "constraint-pair intersection",
                "estimated_operations": 20,
                "estimated_seconds": 0.1,
                "memory_mb": 1,
                "scaling": "quadratic in the number of constraints",
            },
            "spike_spec": {
                "questions": [{"id": "q1.spike.question", "description": "Does the kernel return feasible vertices?"}],
                "representative_cases": [{"id": "q1.spike.case", "description": "Full constraint set"}],
                "required_metrics": [{"id": "q1.spike.metric", "description": "Intersection throughput"}],
                "required_witnesses": [{"id": "q1.spike.witness", "description": "One feasible vertex"}],
            },
        }

    def _write_card(self, workspace: Path, inventory: dict[str, object]) -> dict[str, object]:
        card = self._card(inventory)
        directory = method_version_dir(workspace, "q1", 1)
        self._write(directory / "method_card.json", card)
        (workspace / "reports" / "q1_METHOD_v1.md").write_text("# Method\n", encoding="utf-8")
        return card

    def _write_spike(self, workspace: Path, normalized_card: dict[str, object]) -> None:
        directory = method_version_dir(workspace, "q1", 1) / "spike"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "probe.py").write_text("print('probe')\n", encoding="utf-8")
        self._write(directory / "witness.json", {"x": 1})
        prefix = directory.relative_to(workspace).as_posix()
        self._write(directory / "spike_report.json", {
            "schema_version": 1,
            "status": "candidate",
            "problem_id": "q1",
            "method_spec_sha256": normalized_card["method_spec_sha256"],
            "budget_seconds": 20,
            "actual_runtime_seconds": 0.1,
            "answered_question_ids": ["q1.spike.question"],
            "probe_scope": ["q1.spike.case"],
            "benchmarks": [{
                "metric_id": "q1.spike.metric",
                "name": "intersections",
                "operations": 20,
                "seconds": 0.1,
                "throughput": 200,
                "unit": "intersections/s",
            }],
            "estimated_full_runtime_seconds": 0.1,
            "peak_memory_mb": 1,
            "witnesses": [{
                "witness_id": "q1.spike.witness",
                "type": "feasible_point",
                "summary": "A feasible vertex was found.",
                "artifact_paths": [f"{prefix}/witness.json"],
            }],
            "unresolved_risks": [],
            "artifact_paths": [f"{prefix}/probe.py", f"{prefix}/witness.json"],
        })

    def _accepted_review(self, review_type: str, problem_id: str | None) -> str:
        return json.dumps({
            "schema_version": 1,
            "review_type": review_type,
            "problem_id": problem_id,
            "verdict": "accept",
            "statement_alignment": "pass",
            "method_validity": "pass",
            "implementation_fidelity": "pass",
            "evidence_sufficiency": "pass",
            "issue_class": "none",
            "issues": [],
            "required_repairs": [],
        })

    def _method_review(self) -> str:
        return json.dumps({
            "schema_version": 1,
            "review_type": "method",
            "problem_id": "q1",
            "verdict": "accept",
            "statement_alignment": "pass",
            "method_validity": "pass",
            "computational_feasibility": "pass",
            "evidence_calibration": "pass",
            "validation_independence": "pass",
            "dependency_consistency": "pass",
            "figure_contract": "pass",
            "issue_class": "none",
            "issues": [],
            "required_repairs": [],
            "supplemental_spike": False,
            "supplemental_spike_ids": [],
            "allowed_downgrades": [],
        })

    def test_v3_phase_order_interleaves_planning_and_execution(self) -> None:
        workflow = initial_workflow(
            {"model": "openai/gpt-5.6-sol", "thinking": "high"},
            {"model": "openai/gpt-5.6-luna", "thinking": "high"},
            contract_version=3,
        )
        inventory = self._inventory()
        q2 = json.loads(json.dumps(inventory["problems"][0]))  # type: ignore[index]
        q2.update({
            "id": "q2",
            "label": "Problem 2",
            "depends_on": ["q1"],
            "requested_outputs": [{"id": "q2.output", "statement": "dependent output"}],
        })
        inventory["problems"].append(q2)  # type: ignore[union-attr]
        expand_problem_phases(workflow, inventory)
        self.assertEqual(
            [phase["id"] for phase in workflow["phases"]],
            [
                "inventory", "inventory_audit",
                "method:q1", "spike:q1", "method_audit:q1", "problem:q1",
                "method:q2", "spike:q2", "method_audit:q2", "problem:q2",
                "paper_planning", "diagram", "writing", "verify",
            ],
        )

    def test_inventory_method_and_spike_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            inventory = validate_problem_inventory(workspace, 1)
            self.assertEqual(inventory["problems"][0]["requested_outputs"][0]["id"], "q1.output")
            self._write_card(workspace, inventory)
            card = validate_method_card(workspace, inventory, "q1", 1)
            self.assertEqual(len(card["method_spec_sha256"]), 64)
            self.assertEqual(spike_budget(60), 20)
            self._write_spike(workspace, card)
            report_path = method_version_dir(workspace, "q1", 1) / "spike" / "spike_report.json"
            raw_report = json.loads(report_path.read_text(encoding="utf-8"))
            raw_report["budget_seconds"] = 20.0
            self._write(report_path, raw_report)
            report = validate_spike_report(workspace, card)
            self.assertEqual(report["status"], "candidate")
            self.assertEqual(report["budget_seconds"], 20)

    def test_level_c_cannot_cover_requested_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            inventory = validate_problem_inventory(workspace, 1)
            card = self._card(inventory)
            card["problem"]["claims"][0]["evidence_level"] = "C_exploratory"  # type: ignore[index]
            self._write(method_version_dir(workspace, "q1", 1) / "method_card.json", card)
            with self.assertRaisesRegex(ContractError, "exploratory evidence"):
                validate_method_card(workspace, inventory, "q1", 1)

    def test_inventory_dependency_and_read_only_audit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            invalid = self._inventory()
            invalid["problems"][0]["depends_on"] = ["q2"]  # type: ignore[index]
            self._write(inventory_path := workspace / "planning" / "inventory" / "v1" / "problem_inventory.json", invalid)
            with self.assertRaisesRegex(ContractError, "dependency order"):
                validate_problem_inventory(workspace, 1)
            self._write(inventory_path, self._inventory())
            baseline = workspace_hashes(workspace)
            (workspace / "reports" / "reviewer-write.md").write_text("forbidden", encoding="utf-8")
            self.assertTrue(stage_scope_errors(
                workspace, baseline, "inventory_audit", planning_version=1
            ))

    def test_targeted_supplemental_spike_uses_requested_subset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            inventory = validate_problem_inventory(workspace, 1)
            self._write_card(workspace, inventory)
            card = validate_method_card(workspace, inventory, "q1", 1)
            directory_path = method_version_dir(workspace, "q1", 1) / "spike" / "supplemental"
            directory_path.mkdir(parents=True)
            (directory_path / "probe.py").write_text("print('metric')\n", encoding="utf-8")
            prefix = directory_path.relative_to(workspace).as_posix()
            self._write(directory_path / "spike_report.json", {
                "schema_version": 1,
                "status": "candidate",
                "problem_id": "q1",
                "method_spec_sha256": card["method_spec_sha256"],
                "budget_seconds": 10,
                "actual_runtime_seconds": 0.1,
                "answered_question_ids": [],
                "probe_scope": [],
                "benchmarks": [{
                    "metric_id": "q1.spike.metric",
                    "name": "intersections",
                    "operations": 20,
                    "seconds": 0.1,
                    "throughput": 200,
                    "unit": "intersections/s",
                }],
                "estimated_full_runtime_seconds": 0.1,
                "peak_memory_mb": 1,
                "witnesses": [],
                "unresolved_risks": [],
                "artifact_paths": [f"{prefix}/probe.py"],
            })
            result = validate_spike_report(
                workspace,
                card,
                supplemental=True,
                supplemental_ids={"q1.spike.metric"},
            )
            self.assertEqual(result["benchmarks"][0]["metric_id"], "q1.spike.metric")
            with self.assertRaisesRegex(ContractError, "IDs are missing or invalid"):
                validate_spike_report(
                    workspace,
                    card,
                    supplemental=True,
                    supplemental_ids={"q1.spike.unknown"},
                )

    def test_supplemental_scope_cannot_modify_primary_spike(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            baseline = workspace_hashes(workspace)
            primary = workspace / "planning" / "methods" / "q1" / "v1" / "spike" / "primary.txt"
            supplemental = primary.parent / "supplemental" / "extra.txt"
            primary.parent.mkdir(parents=True, exist_ok=True)
            supplemental.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text("changed", encoding="utf-8")
            supplemental.write_text("allowed", encoding="utf-8")
            errors = stage_scope_errors(
                workspace,
                baseline,
                "spike:q1",
                planning_version=1,
                supplemental_spike=True,
            )
            self.assertTrue(any("primary.txt" in error for error in errors))
            self.assertFalse(any("extra.txt" in error for error in errors))

    def test_method_review_rejects_illegal_downgrade(self) -> None:
        review = json.loads(self._method_review())
        review.update({
            "verdict": "revise",
            "evidence_calibration": "fail",
            "issue_class": "evidence",
            "issues": ["proof level is unaffordable"],
            "required_repairs": ["calibrate the claim"],
            "allowed_downgrades": [{
                "claim_id": "q1.objective",
                "from": "B_bounded_numerical",
                "to": "C_exploratory",
                "reason": "too costly",
            }],
        })
        with self.assertRaisesRegex(ScientificContractError, "only A_certified"):
            parse_method_review(json.dumps(review), problem_id="q1")

    def _runtime_at_method_audit(
        self, directory: str, *, evidence_level: str = "B_bounded_numerical"
    ) -> TaskRuntime:
        workspace = self._workspace(directory)
        inventory = validate_problem_inventory(workspace, 1)
        raw_card = self._card(inventory)
        raw_card["problem"]["claims"][0]["evidence_level"] = evidence_level  # type: ignore[index]
        raw_card["problem"]["claims"][0]["limitations"] = (  # type: ignore[index]
            [] if evidence_level == "A_certified" else ["Finite-domain numerical result."]
        )
        self._write(method_version_dir(workspace, "q1", 1) / "method_card.json", raw_card)
        (workspace / "reports" / "q1_METHOD_v1.md").write_text("# Method\n", encoding="utf-8")
        card = validate_method_card(workspace, inventory, "q1", 1)
        self._write_spike(workspace, card)
        workflow = initial_workflow(
            {"model": "openai/gpt-5.6-sol", "thinking": "high"},
            {"model": "openai/gpt-5.6-luna", "thinking": "high"},
            contract_version=3,
        )
        expand_problem_phases(workflow, inventory)
        for phase in workflow["phases"][:4]:
            phase["status"] = "completed"
        audit_phase = workflow["phases"][4]
        audit_phase.update({"status": "running", "attempts": 1})
        workflow.update({
            "current": "method_audit:q1",
            "mode": "method_audit",
            "inventory_version": 1,
        })
        inventory_file = workspace / "planning" / "inventory" / "v1" / "problem_inventory.json"
        self._write(workspace / "planning" / "ledger.json", {
            "schema_version": 1,
            "inventory": {
                "version": 1,
                "status": "accepted",
                "path": inventory_file.relative_to(workspace).as_posix(),
                "sha256": hashlib.sha256(inventory_file.read_bytes()).hexdigest(),
            },
            "problems": {
                "q1": {
                    "proposal_version": 1,
                    "status": "candidate",
                    "method_spec_sha256": card["method_spec_sha256"],
                    "spike_source_version": 1,
                    "ordinary_audits": 0,
                    "supplemental_used": False,
                    "superseded_versions": [],
                }
            },
            "plan_version": 0,
        })
        snapshot = workspace_hashes(workspace)
        workflow["stage_snapshot"] = snapshot
        workflow["review_snapshot"] = snapshot
        self._write(workspace / "project.json", {
            "status": "running",
            "problem_file": "input/problem.md",
            "competition": "MCM",
            "language": "English",
            "paper_engine": "LaTeX",
            "workflow": workflow,
        })
        runtime = TaskRuntime("f" * 12, workspace, status="running")
        runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
        runtime.prompt = AsyncMock()  # type: ignore[method-assign]
        runtime.system = AsyncMock()  # type: ignore[method-assign]
        return runtime

    def test_method_spec_hash_ignores_claim_wording_but_not_method(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            inventory = validate_problem_inventory(workspace, 1)
            raw = self._card(inventory)
            self._write(method_version_dir(workspace, "q1", 1) / "method_card.json", raw)
            first = validate_method_card(workspace, inventory, "q1", 1)
            raw["problem"]["claims"][0]["statement"] = "Calibrated claim wording."  # type: ignore[index]
            self._write(method_version_dir(workspace, "q1", 1) / "method_card.json", raw)
            wording_only = validate_method_card(workspace, inventory, "q1", 1)
            self.assertEqual(first["method_spec_sha256"], wording_only["method_spec_sha256"])
            raw["problem"]["method"] = "A different executable method."  # type: ignore[index]
            self._write(method_version_dir(workspace, "q1", 1) / "method_card.json", raw)
            changed = validate_method_card(workspace, inventory, "q1", 1)
            self.assertNotEqual(first["method_spec_sha256"], changed["method_spec_sha256"])

    def test_contradictory_failure_semantics_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            inventory = validate_problem_inventory(workspace, 1)
            card = self._card(inventory)
            card["problem"]["failure_semantics"] = [  # type: ignore[index]
                {
                    "id": "q1.failure.first_contact_domain",
                    "event_id": "q1.event.first_contact",
                    "category": "domain_event",
                    "condition": "first contact occurs",
                    "action": "report the event",
                },
                {
                    "id": "q1.failure.first_contact_math",
                    "event_id": "q1.event.first_contact",
                    "category": "mathematical_infeasibility",
                    "condition": "non-adjacent benches first touch",
                    "action": "stop as infeasible",
                },
            ]
            self._write(method_version_dir(workspace, "q1", 1) / "method_card.json", card)
            with self.assertRaisesRegex(ContractError, "conflicting categories"):
                validate_method_card(workspace, inventory, "q1", 1)

    def test_spike_must_cover_every_planned_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            inventory = validate_problem_inventory(workspace, 1)
            self._write_card(workspace, inventory)
            card = validate_method_card(workspace, inventory, "q1", 1)
            self._write_spike(workspace, card)
            report_path = method_version_dir(workspace, "q1", 1) / "spike" / "spike_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["benchmarks"][0]["metric_id"] = "q1.spike.unrelated"
            self._write(report_path, report)
            with self.assertRaisesRegex(ContractError, "metrics coverage mismatch"):
                validate_spike_report(workspace, card)

    async def test_failed_spike_is_not_reused_by_same_spec_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            project = runtime._project()
            workflow = project["workflow"]
            workflow["current"] = "spike:q1"
            workflow["mode"] = "feasibility_spike"
            spike_phase = next(item for item in workflow["phases"] if item["id"] == "spike:q1")
            spike_phase.update({"status": "running", "attempts": 1})
            workflow["stage_snapshot"] = workspace_hashes(runtime.workspace)
            runtime._save_project(project)
            spike_dir = method_version_dir(runtime.workspace, "q1", 1) / "spike"
            for path in spike_dir.iterdir():
                path.unlink()

            await runtime._settled()
            self.assertEqual(runtime._ledger()["problems"]["q1"]["proposal_version"], 2)

            inventory = validate_problem_inventory(runtime.workspace, 1)
            revised = self._card(inventory)
            revised["proposal_version"] = 2
            self._write(method_version_dir(runtime.workspace, "q1", 2) / "method_card.json", revised)
            (runtime.workspace / "reports" / "q1_METHOD_v2.md").write_text("# Same method\n", encoding="utf-8")
            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "spike:q1")
            self.assertFalse(any(
                item.get("reused_from_version")
                for item in saved["phases"]
                if item["id"] == "spike:q1"
            ))

    async def test_inventory_audit_pending_transition_replays_partial_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            workflow = initial_workflow(
                {"model": "openai/gpt-5.6-sol", "thinking": "high"},
                {"model": "openai/gpt-5.6-luna", "thinking": "high"},
                contract_version=3,
            )
            self._write(workspace / "planning" / "ledger.json", {
                "schema_version": 1,
                "inventory": {"version": 1, "status": "candidate"},
                "problems": {},
                "plan_version": 0,
            })
            workflow["phases"][0]["status"] = "completed"
            workflow["phases"][1].update({"status": "running", "attempts": 1})
            workflow["current"] = "inventory_audit"
            workflow["mode"] = "inventory_audit"
            snapshot = workspace_hashes(workspace)
            workflow["stage_snapshot"] = snapshot
            workflow["review_snapshot"] = snapshot
            review = json.loads(self._accepted_review("inventory", None))
            workflow["pending_transition"] = {
                "kind": "inventory_audit",
                "stage": "inventory_audit",
                "review": review,
                "ledger_before": {
                    "schema_version": 1,
                    "inventory": {"version": 1, "status": "candidate"},
                    "problems": {},
                    "plan_version": 0,
                },
            }
            workflow["pending_transition"]["signature"] = bridge._transition_signature(
                "f" * 12, workflow["pending_transition"]
            )
            self._write(workspace / "project.json", {
                "status": "running",
                "problem_file": "input/problem.md",
                "competition": "MCM",
                "language": "English",
                "paper_engine": "LaTeX",
                "workflow": workflow,
            })
            partial = json.loads(json.dumps(workflow["pending_transition"]["ledger_before"]))
            partial["inventory"]["status"] = "accepted"
            self._write(workspace / "planning" / "ledger.json", partial)
            runtime = TaskRuntime("f" * 12, workspace, status="running")
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "method:q1")
            self.assertEqual(runtime._ledger()["inventory"]["status"], "accepted")
            self.assertNotIn("pending_transition", saved)

    async def test_rejected_method_audit_pending_transition_replays_to_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            review = json.loads(self._method_review())
            review.update({
                "verdict": "revise",
                "method_validity": "fail",
                "issue_class": "method",
                "issues": ["The proposed method is not valid on the stated domain."],
                "required_repairs": ["Use a bounded valid method."],
            })
            project = runtime._project()
            transition = {
                "kind": "method_audit",
                "stage": "method_audit:q1",
                "review": review,
                "ledger_before": runtime._ledger(),
            }
            transition["signature"] = bridge._transition_signature(
                runtime.task_id, transition
            )
            project["workflow"]["pending_transition"] = transition
            runtime._save_project(project)
            partial = runtime._ledger()
            partial["problems"]["q1"]["ordinary_audits"] = 99
            runtime._save_ledger(partial)

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "method:q1")
            self.assertEqual(saved["mode"], "method_revision")
            self.assertEqual(runtime._ledger()["problems"]["q1"]["proposal_version"], 2)
            self.assertNotIn("pending_transition", saved)

    async def test_reviewer_forged_pending_transition_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            project = runtime._project()
            project["workflow"]["pending_transition"] = {
                "kind": "method_audit",
                "stage": "method_audit:q1",
                "review": json.loads(self._method_review()),
                "ledger_before": runtime._ledger(),
                "signature": "0" * 64,
            }
            runtime._save_project(project)
            runtime._last_assistant_text = self._method_review()

            await runtime._settled()

            saved = runtime._project()
            self.assertEqual(saved["status"], "failed")
            self.assertIn(
                "invalid or forged",
                saved["workflow"]["phases"][4]["last_error"],
            )

    async def test_reviewer_ledger_write_without_host_transition_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            ledger = runtime._ledger()
            ledger["problems"]["q1"]["status"] = "accepted"
            runtime._save_ledger(ledger)
            runtime._last_assistant_text = self._method_review()

            await runtime._settled()

            saved = runtime._project()
            self.assertEqual(saved["status"], "failed")
            self.assertIn(
                "method reviewer modified workspace",
                saved["workflow"]["phases"][4]["last_error"],
            )

    async def test_method_audit_pending_transition_replays_partial_host_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            review = json.loads(self._method_review())
            project = runtime._project()
            ledger_before = runtime._ledger()
            project["workflow"]["pending_transition"] = {
                "kind": "method_audit",
                "stage": "method_audit:q1",
                "review": review,
                "ledger_before": ledger_before,
            }
            project["workflow"]["pending_transition"]["signature"] = bridge._transition_signature(
                runtime.task_id, project["workflow"]["pending_transition"]
            )
            runtime._save_project(project)
            partial_ledger = runtime._ledger()
            partial_ledger["problems"]["q1"]["ordinary_audits"] = 99
            runtime._save_ledger(partial_ledger)
            audit_path = method_version_dir(runtime.workspace, "q1", 1) / "audit_1.json"
            self._write(audit_path, review)

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "problem:q1")
            self.assertEqual(runtime._ledger()["problems"]["q1"]["ordinary_audits"], 1)
            self.assertNotIn("pending_transition", saved)

    async def test_supplemental_spike_preserves_first_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            review = json.loads(self._method_review())
            review.update({
                "verdict": "revise",
                "computational_feasibility": "fail",
                "issue_class": "budget",
                "issues": ["One kernel measurement is missing."],
                "required_repairs": ["Benchmark the collision kernel."],
                "supplemental_spike": True,
                "supplemental_spike_ids": ["q1.spike.metric"],
            })
            runtime._last_assistant_text = json.dumps(review)
            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["current"], "spike:q1")
            self.assertTrue(workflow["supplemental_spike"])
            self.assertTrue(
                (runtime.workspace / "planning" / "methods" / "q1" / "v1" / "audit_1.json").is_file()
            )
            self.assertEqual(runtime._ledger()["problems"]["q1"]["ordinary_audits"], 1)

    async def test_exhausted_method_audit_allows_only_bounded_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory, evidence_level="A_certified")
            project = runtime._project()
            project["workflow"]["phases"][4]["attempts"] = 3
            runtime._save_project(project)
            review = json.loads(self._method_review())
            review.update({
                "verdict": "revise",
                "evidence_calibration": "fail",
                "issue_class": "evidence",
                "issues": ["Certification is unnecessary and unaffordable."],
                "required_repairs": ["Scope the result as bounded numerical evidence."],
                "allowed_downgrades": [{
                    "claim_id": "q1.objective",
                    "from": "A_certified",
                    "to": "B_bounded_numerical",
                    "reason": "The requested output is numerical.",
                }],
            })
            runtime._last_assistant_text = json.dumps(review)
            await runtime._settled()
            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["mode"], "evidence_downgrade")
            self.assertEqual(runtime._ledger()["problems"]["q1"]["proposal_version"], 2)

            inventory = validate_problem_inventory(runtime.workspace, 1)
            revised = self._card(inventory)
            revised["proposal_version"] = 2
            revised["problem"]["claims"][0]["statement"] = "The finite-domain estimate is best at the declared resolution."  # type: ignore[index]
            revised["problem"]["claims"][0]["limitations"] = ["No claim is made outside the finite domain."]  # type: ignore[index]
            self._write(method_version_dir(runtime.workspace, "q1", 2) / "method_card.json", revised)
            (runtime.workspace / "reports" / "q1_METHOD_v2.md").write_text("# Bounded method\n", encoding="utf-8")
            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "spike:q1")
            spike_phase = next(item for item in saved["phases"] if item["id"] == "spike:q1")
            self.assertFalse(spike_phase.get("reused_from_version"))

    async def test_v3_pause_resume_keeps_spike_attempt_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._runtime_at_method_audit(directory)
            project = runtime._project()
            workflow = project["workflow"]
            workflow["current"] = "spike:q1"
            workflow["mode"] = "feasibility_spike"
            spike_phase = next(item for item in workflow["phases"] if item["id"] == "spike:q1")
            spike_phase.update({"status": "running", "attempts": 1})
            workflow["stage_snapshot"] = workspace_hashes(runtime.workspace)
            workflow["spike_elapsed_seconds"] = 15.2
            runtime._save_project(project)
            self.assertEqual(runtime._current_runtime_limit(), 5)
            runtime.process = SimpleNamespace(returncode=None)  # type: ignore[assignment]
            runtime.send_rpc = AsyncMock()  # type: ignore[method-assign]
            runtime.terminate = AsyncMock()  # type: ignore[method-assign]

            await runtime.pause()
            runtime.process = None
            runtime.run = AsyncMock()  # type: ignore[method-assign]
            await runtime.resume()
            await asyncio.sleep(0)

            saved = runtime._project()["workflow"]
            saved_spike = next(item for item in saved["phases"] if item["id"] == "spike:q1")
            self.assertEqual(saved_spike["attempts"], 1)
            self.assertEqual(saved["mode"], "feasibility_spike")
            self.assertEqual(runtime.requested_model, "openai/gpt-5.6-luna")
            self.assertIn("feasibility Spike", runtime.run.call_args.args[0])
            runtime.runner.cancel()

    async def test_orphaned_running_v3_project_loads_as_paused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "1" * 12
            workspace = Path(directory) / task_id
            workspace.mkdir()
            self._write(workspace / "project.json", {
                "status": "running",
                "runtime_owner_pid": 999999999,
                "workflow": {
                    "contract_version": 3,
                    "current": "inventory",
                    "mode": "inventory",
                    "phases": [{"id": "inventory", "status": "running", "last_error": ""}],
                },
            })
            TASKS.clear()
            with patch.object(bridge, "WORKSPACES", Path(directory)):
                runtime = bridge._runtime(task_id)
            self.assertEqual(runtime.status, "paused")
            self.assertEqual(runtime._project()["pause_reason"], "bridge_restart")
            TASKS.clear()

    async def test_v3_incremental_flow_reaches_paper_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            workflow = initial_workflow(
                {"model": "openai/gpt-5.6-sol", "thinking": "high"},
                {"model": "openai/gpt-5.6-luna", "thinking": "high"},
                contract_version=3,
            )
            self._write(workspace / "planning" / "ledger.json", {
                "schema_version": 1,
                "inventory": {"version": 1, "status": "candidate"},
                "problems": {},
                "plan_version": 0,
            })
            workflow["stage_snapshot"] = workspace_hashes(workspace)
            self._write(workspace / "project.json", {
                "status": "running",
                "problem_file": "input/problem.md",
                "competition": "MCM",
                "language": "English",
                "paper_engine": "LaTeX",
                "workflow": workflow,
            })
            runtime = TaskRuntime("f" * 12, workspace, status="running")
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()
            self.assertEqual(runtime._project()["workflow"]["mode"], "inventory_audit")
            runtime._last_assistant_text = self._accepted_review("inventory", None)
            await runtime._settled()
            self.assertEqual(runtime._project()["workflow"]["current"], "method:q1")

            inventory = validate_problem_inventory(workspace, 1)
            self._write_card(workspace, inventory)
            await runtime._settled()
            self.assertEqual(runtime._project()["workflow"]["current"], "spike:q1")

            card = validate_method_card(workspace, inventory, "q1", 1)
            self._write_spike(workspace, card)
            await runtime._settled()
            self.assertEqual(runtime._project()["workflow"]["current"], "method_audit:q1")

            runtime._last_assistant_text = self._method_review()
            await runtime._settled()
            self.assertEqual(runtime._project()["workflow"]["current"], "problem:q1")
            self.assertEqual(runtime._ledger()["problems"]["q1"]["status"], "provisional")
            plan = validate_execution_plan(workspace)
            self.assertEqual(plan["problems"][0]["claims"][0]["evidence_level"], "B_bounded_numerical")

            (workspace / "code" / "q1").mkdir()
            (workspace / "results" / "q1").mkdir()
            (workspace / "code" / "q1" / "solve.py").write_text("print(1)\n", encoding="utf-8")
            (workspace / "reports" / "q1_RESULTS.md").write_text("candidate", encoding="utf-8")
            self._write(workspace / "results" / "q1" / "evidence.json", {"value": 1})
            self._write(workspace / "results" / "q1" / "result.json", {
                "problem_id": "q1",
                "status": "candidate",
                "metrics": [{"name": "objective", "value": 1, "unit": "", "description": "test"}],
            })
            self._write(workspace / "results" / "q1" / "verification.json", {
                "schema_version": 2,
                "status": "candidate",
                "smoke_runtime_seconds": 0.01,
                "estimated_runtime_seconds": 0.1,
                "actual_runtime_seconds": 0.1,
                "checks": ["all vertices"],
                "figures": [],
                "claim_evidence": [{
                    "claim_id": "q1.objective",
                    "status": "supported",
                    "independent": True,
                    "method": "separate enumeration",
                    "evidence_paths": ["results/q1/evidence.json"],
                }],
            })
            await runtime._settled()
            self.assertEqual(runtime._project()["workflow"]["mode"], "scientific_review")
            accepted_text = self._accepted_review("scientific", "q1")
            accepted_review = json.loads(accepted_text)
            ledger_before = runtime._ledger()
            project = runtime._project()
            project["workflow"]["pending_transition"] = {
                "kind": "scientific_review",
                "stage": "problem:q1",
                "review": accepted_review,
                "ledger_before": ledger_before,
            }
            project["workflow"]["pending_transition"]["signature"] = bridge._transition_signature(
                runtime.task_id, project["workflow"]["pending_transition"]
            )
            runtime._save_project(project)
            review_path = workspace / "reports" / "q1_SCIENTIFIC_REVIEW.json"
            review_path.write_text(
                json.dumps(json.loads(accepted_text), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            ledger = runtime._ledger()
            ledger["problems"]["q1"]["status"] = "accepted"
            ledger["problems"]["q1"]["scientific_candidate_sha256"] = artifact_hashes(
                workspace, "q1"
            )
            runtime._save_ledger(ledger)
            runtime._last_assistant_text = accepted_text
            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "paper_planning", saved)
            self.assertEqual(runtime._ledger()["problems"]["q1"]["status"], "accepted")
            receipt = json.loads(
                (workspace / "reports" / "PLAN_COMPLETENESS.json").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["status"], "pass")
            expected, errors = plan_completeness_receipt(
                workspace, validate_execution_plan(workspace), runtime._ledger()
            )
            self.assertEqual(errors, [])
            self.assertEqual(receipt, expected)
            tampered = json.loads(json.dumps(validate_execution_plan(workspace)))
            tampered["problems"][0]["method"] = "tampered"
            _, tamper_errors = plan_completeness_receipt(
                workspace, tampered, runtime._ledger()
            )
            self.assertTrue(any("differs from active method card" in error for error in tamper_errors))
            spike_artifact = next(
                path
                for path in runtime._ledger()["problems"]["q1"]["spike"]["artifact_sha256"]
                if path.endswith("witness.json")
            )
            (workspace / spike_artifact).write_text("tampered", encoding="utf-8")
            _, spike_tamper_errors = plan_completeness_receipt(
                workspace, validate_execution_plan(workspace), runtime._ledger()
            )
            self.assertTrue(any("spike artifact hash mismatch" in error for error in spike_tamper_errors))


class BridgeHelpersTest(unittest.TestCase):
    """Cover path safety, progress discovery, and prompt construction."""

    def test_safe_file_stays_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            expected = workspace / "result.json"
            expected.write_text("{}", encoding="utf-8")

            self.assertEqual(_safe_file(workspace, "result.json"), expected.resolve())
            with self.assertRaises(HTTPException):
                _safe_file(workspace, "../outside.txt")

    def test_visible_files_hide_bridge_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "results").mkdir()
            (workspace / "results" / "answer.csv").write_text("x\n1\n")
            (workspace / ".pi-bridge").mkdir()
            (workspace / ".pi-bridge" / "messages.json").write_text("[]")

            self.assertEqual(
                [item["filename"] for item in _visible_files(workspace)],
                ["results/answer.csv"],
            )

    def test_upload_path_preserves_folder_structure(self) -> None:
        self.assertEqual(
            _upload_path("A题/附件/result1.xlsx", "A题").as_posix(),
            "附件/result1.xlsx",
        )
        for unsafe in (
            "A题/../secret.txt",
            "C:/escape.txt",
            "C:escape.txt",
            r"\\server\share\escape.txt",
            "/absolute/escape.txt",
            "safe.txt:alternate-stream",
            "nul\x00name.txt",
        ):
            with self.subTest(path=unsafe), self.assertRaises(HTTPException):
                _upload_path(unsafe, "A题")

    def test_plan_revision_prompt_contains_audit_without_tool_artifacts(self) -> None:
        prompt = plan_revision_prompt({"issues": ["finite event witness missing"]})
        self.assertIn("finite event witness missing", prompt)
        self.assertIn("cheapest scientifically honest repair", prompt)
        self.assertNotIn("oldText", prompt)

    def test_protocol_text_excludes_hidden_thinking(self) -> None:
        content = [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": '{"verdict":"accept"}'},
        ]
        self.assertEqual(_visible_text(content), '{"verdict":"accept"}')

    def test_verification_requires_explicit_pass_conclusion(self) -> None:
        self.assertTrue(_verification_passed("## Final status: PASS\n"))
        self.assertTrue(_verification_passed("验收结论：PASS\n"))
        self.assertFalse(_verification_passed("One check did not PASS.\n"))

    def test_document_stack_checks_selected_engine_and_pdf_renderer(self) -> None:
        available = {"xelatex": "xelatex.exe", "pdftoppm": "pdftoppm.exe"}
        with patch.object(bridge.shutil, "which", side_effect=available.get):
            self.assertEqual(_document_stack_errors("LaTeX"), [])
            self.assertEqual(
                _document_stack_errors("Typst"),
                ["document_preflight: missing typst for Typst"],
            )

    def test_model_config_accepts_supported_values(self) -> None:
        self.assertEqual(
            _task_model_config("openai/gpt-5.6-sol", "xhigh"),
            ("openai/gpt-5.6-sol", "xhigh"),
        )

    def test_model_config_rejects_invalid_values(self) -> None:
        with self.assertRaises(HTTPException):
            _task_model_config("openai/model with spaces", "high")
        with self.assertRaises(HTTPException):
            _task_model_config("openai/gpt-5.6-sol", "extreme")

    def test_model_config_uses_bridge_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MATHMODEL_PI_MODEL": "openai/gpt-5.6-luna",
                "MATHMODEL_PI_THINKING": "medium",
            },
        ):
            self.assertEqual(
                _task_model_config("", ""),
                ("openai/gpt-5.6-luna", "medium"),
            )

    def test_phase_status_uses_todo_checkboxes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "todo.md").write_text(
                "- [x] 1. analysis\n- [ ] 2. coding\n", encoding="utf-8"
            )

            phases = _phase_statuses(workspace, "running")
            self.assertEqual(phases[0]["status"], "completed")
            self.assertEqual(phases[1]["status"], "running")
            self.assertEqual(phases[2]["status"], "pending")


class SimpleBakeryOracleTest(unittest.TestCase):
    def test_oracle_accepts_exact_fixture_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for problem_id, values in {
                "q1": {"bread_units": 40, "cake_units": 20, "max_profit": 2000},
                "q2": {
                    "bread_units": 140 / 3, "cake_units": 50 / 3,
                    "max_profit": 6200 / 3, "profit_increase": 200 / 3,
                },
                "q3": {"scenario_count": 5},
            }.items():
                result_dir = workspace / "results" / problem_id
                result_dir.mkdir(parents=True)
                (result_dir / "result.json").write_text(json.dumps({
                    "problem_id": problem_id, "status": "pass", "metrics": [
                        {"name": name, "value": value, "unit": "", "description": name}
                        for name, value in values.items()
                    ]
                }))
            (workspace / "results" / "q3" / "sensitivity.csv").write_text(
                "flour_capacity,labor_capacity,bread_units,cake_units,max_profit\n"
                "80,80,26.6666666667,26.6666666667,1866.6666666667\n"
                "90,80,33.3333333333,23.3333333333,1933.3333333333\n"
                "100,80,40,20,2000\n"
                "110,80,46.6666666667,16.6666666667,2066.6666666667\n"
                "120,80,53.3333333333,13.3333333333,2133.3333333333\n"
            )
            (workspace / "figures" / "q3").mkdir(parents=True)
            (workspace / "figures" / "q3" / "profit.png").write_bytes(b"png")
            (workspace / "paper").mkdir()
            (workspace / "paper" / "main.pdf").write_bytes(b"pdf")
            (workspace / "reports").mkdir()
            (workspace / "reports" / "VERIFY_REPORT.md").write_text("Conclusion: PASS\n")

            validate_simple_bakery(workspace)


class ProjectInitializationTest(unittest.IsolatedAsyncioTestCase):
    """Verify official contest folders become ready workspaces without Pi."""

    async def asyncTearDown(self) -> None:
        TASKS.clear()

    async def test_start_preflight_failure_keeps_project_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(bridge, "WORKSPACES", root):
                runtime, summary = await _initialize_project(
                    question="",
                    source_folder="fixture",
                    files=[UploadFile(filename="problem.md", file=BytesIO(b"problem"))],
                    relative_paths=["fixture/problem.md"],
                )
                with (
                    patch.object(
                        bridge,
                        "figure_stack_errors",
                        return_value=["figure_preflight: missing SciencePlots==2.2.2"],
                    ),
                    self.assertRaises(HTTPException) as raised,
                ):
                    await bridge._start_project(
                        runtime,
                        bridge.StartProjectRequest(
                            problem_file=str(summary["problem_file"]),
                            language="Chinese",
                        ),
                    )
            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(runtime.status, "ready")
            project = json.loads((runtime.workspace / "project.json").read_text())
            self.assertEqual(project["status"], "ready")

    async def test_initialize_cumcm_folder_preserves_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uploads = [
                UploadFile(filename="A题.pdf", file=BytesIO(b"pdf")),
                UploadFile(filename="result1.xlsx", file=BytesIO(b"xlsx")),
            ]
            with patch.object(bridge, "WORKSPACES", root):
                runtime, summary = await _initialize_project(
                    question="",
                    source_folder="A题",
                    files=uploads,
                    relative_paths=["A题/A题.pdf", "A题/附件/result1.xlsx"],
                )

            self.assertEqual(runtime.status, "ready")
            self.assertIsNone(runtime.process)
            self.assertEqual(summary["problem_file"], "input/A题.pdf")
            self.assertEqual(summary["datasets"], ["input/附件/result1.xlsx"])
            self.assertTrue((runtime.workspace / "input" / "A题.pdf").is_file())
            self.assertTrue(
                (runtime.workspace / "input" / "附件" / "result1.xlsx").is_file()
            )
            self.assertTrue((runtime.workspace / "project.json").is_file())
            self.assertTrue((runtime.workspace / "input_manifest.json").is_file())
            self.assertTrue((runtime.workspace / "todo.md").is_file())
            for directory_name in bridge.SCAFFOLD_DIRS:
                self.assertTrue((runtime.workspace / directory_name).is_dir())


class ScientificRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def _problem(self, problem_id: str = "q1") -> dict[str, object]:
        claim_id = f"{problem_id}.objective"
        return {
            "id": problem_id,
            "label": f"Problem {problem_id}",
            "depends_on": [],
            "method": "enumerate all feasible vertices",
            "inputs": ["input/problem.md", "reports/ANALYSIS_MODELING_REPORT.md"],
            "outputs": [
                f"code/{problem_id}/solve.py",
                f"results/{problem_id}/result.json",
                f"reports/{problem_id}_RESULTS.md",
            ],
            "validation": ["independent vertex check"],
            "runtime_limit_seconds": 60,
            "requested_outputs": ["optimal production and objective"],
            "interpretation": "Use continuous nonnegative quantities requested by the statement.",
            "assumptions": [],
            "claims": [{
                "id": claim_id, "type": "optimality",
                "statement": "The candidate is globally optimal.",
                "evidence_required": ["feasibility", "all vertices"],
                "acceptance": {"criterion": "No feasible vertex is better.", "tolerance": 1e-9},
            }],
            "approximations": [],
            "failure_semantics": [{
                "condition": "solver error", "classification": "numerical_error",
                "action": "repair; do not call the model infeasible",
            }],
            "independent_validation": [{
                "id": f"{problem_id}.check", "method": "enumerate all vertices",
                "independent_from": "closed-form active-set candidate", "claims": [claim_id],
            }],
        }

    def _workspace(self, directory: str, *, at_problem: bool = False) -> TaskRuntime:
        workspace = Path(directory)
        for name in ("input", "reports", "code", "results", "figures", "paper"):
            (workspace / name).mkdir()
        (workspace / "input" / "problem.md").write_text("problem")
        (workspace / "reports" / "ANALYSIS_MODELING_REPORT.md").write_text("analysis")
        plan = {"schema_version": 2, "plan_version": 1, "problems": [self._problem()]}
        (workspace / "execution_plan.json").write_text(json.dumps(plan))
        workflow = initial_workflow(
            {"model": "openai/gpt-5.6-sol", "thinking": "high"},
            {"model": "openai/gpt-5.6-luna", "thinking": "high"},
            contract_version=2,
        )
        if at_problem:
            workflow["phases"][0]["status"] = "completed"
            workflow["phases"][1]["status"] = "completed"
            expand_problem_phases(workflow, plan)
            workflow["current"] = "problem:q1"
            workflow["mode"] = "execute"
            workflow["phases"][2].update({"status": "running", "attempts": 1})
        workflow["stage_snapshot"] = workspace_hashes(workspace)
        (workspace / "project.json").write_text(json.dumps({
            "status": "running", "problem_file": "input/problem.md",
            "competition": "MCM", "language": "English", "paper_engine": "LaTeX",
            "workflow": workflow,
        }))
        return TaskRuntime("c" * 12, workspace, status="running")

    def _candidate(self, runtime: TaskRuntime) -> None:
        code = runtime.workspace / "code" / "q1"
        results = runtime.workspace / "results" / "q1"
        code.mkdir()
        results.mkdir()
        (code / "solve.py").write_text("print(1)\n")
        (runtime.workspace / "reports" / "q1_RESULTS.md").write_text("candidate")
        (results / "evidence.json").write_text("{}")
        (results / "result.json").write_text(json.dumps({
            "problem_id": "q1", "status": "candidate", "metrics": [{
                "name": "objective", "value": 1, "unit": "", "description": "test"
            }]
        }))
        (results / "verification.json").write_text(json.dumps({
            "schema_version": 2, "status": "candidate",
            "smoke_runtime_seconds": 0.01, "estimated_runtime_seconds": 0.1,
            "actual_runtime_seconds": 0.1, "checks": ["all vertices"],
            "figures": [],
            "claim_evidence": [{
                "claim_id": "q1.objective", "status": "supported",
                "independent": True, "method": "vertex enumeration",
                "evidence_paths": ["results/q1/evidence.json"],
            }],
        }))

    def _review(self, verdict: str = "accept", issue_class: str = "none") -> str:
        accepted = verdict == "accept"
        return json.dumps({
            "schema_version": 1, "review_type": "scientific", "problem_id": "q1",
            "verdict": verdict,
            "statement_alignment": "pass" if accepted else "fail",
            "method_validity": "pass" if accepted else "fail",
            "implementation_fidelity": "pass" if accepted else "fail",
            "evidence_sufficiency": "pass" if accepted else "fail",
            "issue_class": issue_class,
            "issues": [] if accepted else ["candidate does not establish the claim"],
            "required_repairs": [] if accepted else ["add independent evidence"],
        })

    def _plan_review(self) -> str:
        return json.dumps({
            "schema_version": 1, "review_type": "plan", "problem_id": None,
            "verdict": "accept", "statement_alignment": "pass",
            "method_validity": "pass", "implementation_fidelity": "pass",
            "evidence_sufficiency": "pass", "issue_class": "none",
            "issues": [], "required_repairs": [],
        })

    def _paper_plan(self, runtime: TaskRuntime) -> None:
        (runtime.workspace / "paper_plan.json").write_text(json.dumps({
            "schema_version": 1, "plan_version": 1,
            "recommended_page_range": [8, 14],
            "coverage": [{
                "claim_id": "q1.objective", "problem_id": "q1", "section_id": "q1",
                "interpretation_and_assumptions": "Explain the selected interpretation.",
                "model_or_equations": ["objective", "constraints"],
                "algorithm_and_stopping": "Enumerate all vertices.",
                "result_evidence": ["results/q1/result.json"],
                "validation_evidence": ["results/q1/evidence.json"],
                "sensitivity_or_robustness": "Perturb capacity.",
                "approximation_ids": [],
                "limitations": ["Only valid under the stated assumptions."], "figures": [],
                "citations_needed": [],
            }],
        }))
        (runtime.workspace / "reports" / "PAPER_PLAN.md").write_text("coverage")

    async def test_pause_persists_without_consuming_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            runtime.send_rpc = AsyncMock()  # type: ignore[method-assign]
            runtime.terminate = AsyncMock()  # type: ignore[method-assign]
            runtime.process = SimpleNamespace(returncode=None)  # type: ignore[assignment]
            before = runtime._project()["workflow"]["phases"][2]["attempts"]

            await runtime.pause()

            project = runtime._project()
            self.assertEqual(runtime.status, "paused")
            self.assertEqual(project["status"], "paused")
            self.assertEqual(project["pause_count"], 1)
            self.assertEqual(project["workflow"]["phases"][2]["status"], "paused")
            self.assertEqual(project["workflow"]["phases"][2]["attempts"], before)
            runtime.send_rpc.assert_any_await({"type": "abort"})
            runtime.terminate.assert_awaited_once()

    async def test_resume_restarts_current_mode_with_frozen_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            project = runtime._project()
            project["status"] = "paused"
            project["workflow"]["mode"] = "scientific_review"
            runtime._save_project(project)
            runtime.status = "paused"
            runtime.run = AsyncMock()  # type: ignore[method-assign]

            await runtime.resume()
            await asyncio.sleep(0)

            saved = runtime._project()
            self.assertEqual(saved["status"], "starting")
            self.assertEqual(saved["resume_count"], 1)
            self.assertEqual(saved["workflow"]["phases"][2]["status"], "running")
            self.assertEqual(runtime.requested_model, "openai/gpt-5.6-sol")
            prompt = runtime.run.call_args.args[0]
            self.assertIn("independent scientific acceptance reviewer", prompt)
            runtime.runner.cancel()

    async def test_orphaned_running_v2_project_loads_as_paused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "d" * 12
            workspace = Path(directory) / task_id
            workspace.mkdir()
            (workspace / "project.json").write_text(json.dumps({
                "status": "running", "runtime_owner_pid": 999999999,
                "workflow": {"contract_version": 2, "phases": []},
            }))
            TASKS.clear()
            with patch.object(bridge, "WORKSPACES", Path(directory)):
                runtime = bridge._runtime(task_id)
            self.assertEqual(runtime.status, "paused")
            self.assertEqual(json.loads((workspace / "project.json").read_text())["status"], "paused")
            TASKS.clear()

    async def test_planning_always_enters_plan_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["current"], "plan_audit")
            self.assertEqual(workflow["mode"], "plan_audit")
            self.assertEqual(workflow["frozen"], {})
            runtime._switch_session.assert_awaited_once_with("planner")

    async def test_plan_audit_accept_expands_problem_phases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = self._plan_review()
            runtime._begin_current = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["current"], "problem:q1")
            self.assertEqual(workflow["phases"][1]["status"], "completed")
            self.assertTrue((runtime.workspace / "reports" / "PLAN_AUDIT.json").is_file())

    async def test_plan_reject_routes_one_planner_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = json.dumps({
                "schema_version": 1, "review_type": "plan", "problem_id": None,
                "verdict": "reject", "statement_alignment": "fail",
                "method_validity": "fail", "implementation_fidelity": "pass",
                "evidence_sufficiency": "fail", "issue_class": "method",
                "issues": ["missing evidence obligation"],
                "required_repairs": ["add independent validation"],
            })
            runtime._switch_session.reset_mock()

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["current"], "planning")
            self.assertEqual(workflow["mode"], "plan_revision")
            self.assertEqual(workflow["phases"][0]["attempts"], 2)
            runtime._switch_session.assert_awaited_once_with("planner")

    async def test_candidate_gate_enters_review_without_freezing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["mode"], "scientific_review")
            self.assertEqual(workflow["frozen"], {})
            runtime._switch_session.assert_awaited_once_with("planner")

    async def test_scientific_accept_freezes_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = self._review()
            runtime._begin_current = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["current"], "paper_planning")
            self.assertIn("q1", workflow["frozen"])
            self.assertEqual(workflow["phases"][2]["scientific_status"], "accepted")

    async def test_method_reject_replans_current_and_downstream_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = self._review("reject", "method")
            await runtime._settled()
            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["mode"], "method_replan")
            revised = json.loads(json.dumps(workflow["replan_base"]["problems"]))
            revised[0]["method"] = "replanned exhaustive method"
            (runtime.workspace / "execution_plan.revision.json").write_text(json.dumps({
                "schema_version": 1,
                "base_plan_version": workflow["replan_base"]["plan_version"],
                "revised_problems": revised,
            }))
            runtime._switch_session.reset_mock()
            runtime.prompt.reset_mock()

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["mode"], "execute")
            self.assertEqual(saved["plan_version"], 2)
            self.assertEqual(validate_execution_plan(runtime.workspace)["plan_version"], 2)
            self.assertFalse((runtime.workspace / "execution_plan.revision.json").exists())
            runtime._switch_session.assert_awaited_once_with("worker")

    async def test_complete_v2_workflow_reaches_document_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            runtime.terminate = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()
            runtime._last_assistant_text = self._plan_review()
            await runtime._settled()
            self._candidate(runtime)
            await runtime._settled()
            runtime._last_assistant_text = self._review()
            await runtime._settled()
            self._paper_plan(runtime)
            await runtime._settled()
            (runtime.workspace / "reports" / "DRAWIO_REPORT.md").write_text("No diagram required.")
            await runtime._settled()

            source = runtime.workspace / "paper" / "q1.tex"
            source.write_text(
                "MODEL ANCHOR ALGORITHM ANCHOR RESULT ANCHOR "
                "VALIDATION ANCHOR CONCLUSION ANCHOR LIMITATION ANCHOR"
            )
            (runtime.workspace / "paper" / "paper_manifest.json").write_text(json.dumps({
                "schema_version": 1, "plan_version": 1,
                "coverage": [{
                    "claim_id": "q1.objective", "section_file": "paper/q1.tex",
                    "anchors": {
                        "model": "MODEL ANCHOR", "algorithm": "ALGORITHM ANCHOR",
                        "result": "RESULT ANCHOR", "validation": "VALIDATION ANCHOR",
                        "conclusion": "CONCLUSION ANCHOR",
                        "limitation": "LIMITATION ANCHOR",
                    },
                    "figures": [],
                }],
            }))
            (runtime.workspace / "paper" / "main.pdf").write_bytes(b"pdf")
            with patch.object(bridge, "_paper_readable", return_value=True):
                await runtime._settled()
                (runtime.workspace / "reports" / "VERIFY_REPORT.md").write_text(
                    "Conclusion: PASS\n"
                )
                await runtime._settled()

            project = runtime._project()
            self.assertEqual(project["status"], "completed")
            self.assertNotEqual(project["status"], "waiting")
            self.assertTrue(all(
                phase["status"] == "completed" for phase in project["workflow"]["phases"]
            ))
            runtime.terminate.assert_awaited_once()

    async def test_paper_planning_gate_advances_to_diagram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = self._review()
            runtime._begin_current = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            self._paper_plan(runtime)

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["current"], "diagram")
            paper_phase = next(item for item in workflow["phases"] if item["id"] == "paper_planning")
            self.assertEqual(paper_phase["status"], "completed")

    async def test_scientific_reject_routes_evidence_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = self._review("reject", "evidence")
            runtime._switch_session.reset_mock()

            await runtime._settled()

            workflow = runtime._project()["workflow"]
            self.assertEqual(workflow["mode"], "scientific_repair")
            self.assertEqual(workflow["phases"][2]["attempts"], 2)
            runtime._switch_session.assert_awaited_once_with("worker")

    async def test_scientific_reviewer_write_fails_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            (runtime.workspace / "code" / "q1" / "solve.py").write_text("reviewer edit")
            runtime._last_assistant_text = self._review()

            await runtime._settled()

            self.assertEqual(runtime.status, "failed")
            self.assertIn(
                "reviewer modified",
                runtime._project()["workflow"]["phases"][2]["last_error"],
            )

    async def test_unresolved_blocked_review_fails_after_replan_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            self._candidate(runtime)
            project = runtime._project()
            project["workflow"]["phases"][2]["replan_attempts"] = 1
            runtime._save_project(project)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._last_assistant_text = self._review("blocked", "blocked")

            await runtime._settled()

            self.assertEqual(runtime.status, "failed")
            self.assertNotEqual(runtime._project()["status"], "waiting")

    async def test_paper_plan_missing_coverage_gets_one_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            project = runtime._project()
            workflow = project["workflow"]
            workflow["current"] = "paper_planning"
            paper_phase = next(item for item in workflow["phases"] if item["id"] == "paper_planning")
            paper_phase.update({"status": "running", "attempts": 1})
            workflow["stage_snapshot"] = workspace_hashes(runtime.workspace)
            runtime._save_project(project)
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            saved = runtime._project()["workflow"]
            saved_phase = next(item for item in saved["phases"] if item["id"] == "paper_planning")
            self.assertEqual(saved["mode"], "paper_plan_repair")
            self.assertEqual(saved_phase["attempts"], 2)
            runtime.prompt.assert_awaited_once()

    async def test_writing_manifest_gets_two_bounded_repairs_then_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            project = runtime._project()
            workflow = project["workflow"]
            workflow["current"] = "writing"
            writing = next(item for item in workflow["phases"] if item["id"] == "writing")
            writing.update({"status": "running", "attempts": 1})
            runtime._save_project(project)
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._repair_current(["validation_failed: figures must be a list"])
            self.assertEqual(runtime._project()["workflow"]["mode"], "paper_manifest_repair")
            self.assertEqual(runtime._project()["workflow"]["phases"][-2]["attempts"], 2)
            await runtime._repair_current(["validation_failed: figures must be a list"])
            self.assertEqual(runtime._project()["workflow"]["phases"][-2]["attempts"], 3)
            await runtime._repair_current(["validation_failed: figures must be a list"])
            self.assertEqual(runtime.status, "failed")

    async def test_v2_budget_exhaustion_fails_not_waits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._workspace(directory, at_problem=True)
            project = runtime._project()
            project["workflow"]["phases"][2]["attempts"] = 3
            runtime._save_project(project)
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            self.assertEqual(runtime.status, "failed")
            self.assertEqual(runtime._project()["status"], "failed")


class StagedRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def _make_runtime(self, directory: str) -> tuple[TaskRuntime, dict[str, object]]:
        workspace = Path(directory)
        for name in ("input", "reports", "code", "results", "figures", "paper"):
            (workspace / name).mkdir()
        (workspace / "input" / "problem.md").write_text("problem", encoding="utf-8")
        problem: dict[str, object] = {
            "id": "q1", "label": "Problem 1", "depends_on": [],
            "method": "enumerate", "inputs": ["input/problem.md"],
            "outputs": ["code/q1/solve.py", "results/q1/result.json", "reports/q1_RESULTS.md"],
            "validation": ["independent check"], "runtime_limit_seconds": 60,
        }
        plan = {"schema_version": 1, "problems": [problem]}
        (workspace / "execution_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        workflow = initial_workflow(
            {"model": "openai/gpt-5.6-sol", "thinking": "high"},
            {"model": "openai/gpt-5.6-luna", "thinking": "high"},
        )
        workflow["phases"][0]["status"] = "completed"
        expand_problem_phases(workflow, plan)
        workflow["current"] = "problem:q1"
        workflow["phases"][1].update({"status": "running", "attempts": 1})
        (workspace / "project.json").write_text(json.dumps({
            "status": "running", "problem_file": "input/problem.md",
            "competition": "MCM", "language": "English", "paper_engine": "LaTeX",
            "workflow": workflow,
        }), encoding="utf-8")
        return TaskRuntime("a" * 12, workspace, status="running"), problem

    async def test_planning_gate_expands_problem_phases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime, _ = self._make_runtime(directory)
            project = runtime._project()
            workflow = initial_workflow(
                {"model": "openai/gpt-5.6-sol", "thinking": "high"},
                {"model": "openai/gpt-5.6-luna", "thinking": "high"},
            )
            project["workflow"] = workflow
            (runtime.workspace / "reports" / "ANALYSIS_MODELING_REPORT.md").write_text("analysis")
            runtime._save_project(project)
            runtime._begin_current = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "problem:q1")
            self.assertEqual(saved["phases"][0]["status"], "completed")
            runtime._begin_current.assert_awaited_once()

    async def test_problem_failure_uses_direct_then_review_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime, _ = self._make_runtime(directory)
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()
            saved = runtime._project()["workflow"]
            self.assertEqual(saved["mode"], "direct_repair")
            self.assertEqual(saved["phases"][1]["attempts"], 2)
            runtime.prompt.assert_awaited_once()

            runtime._start_review = AsyncMock()  # type: ignore[method-assign]
            await runtime._settled()
            runtime._start_review.assert_awaited_once()

    async def test_review_handoff_starts_final_worker_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime, _ = self._make_runtime(directory)
            project = runtime._project()
            workflow = project["workflow"]
            workflow["mode"] = "review"
            workflow["review_snapshot"] = workspace_hashes(runtime.workspace)
            runtime._save_project(project)
            runtime._last_assistant_text = "Root cause: wrong predicate. Change q1 solver."
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["mode"], "final_repair")
            self.assertEqual(saved["phases"][1]["attempts"], 3)
            self.assertTrue((runtime.workspace / "reports" / "q1_REPAIR_REVIEW.md").is_file())
            runtime._switch_session.assert_awaited_once_with("worker")

    async def test_three_problem_workflow_reaches_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for name in ("input", "reports", "code", "results", "figures", "paper"):
                (workspace / name).mkdir()
            (workspace / "input" / "problem.md").write_text("three questions")
            problems = []
            for index in range(1, 4):
                problem_id = f"q{index}"
                dependencies = [f"q{index - 1}"] if index > 1 else []
                inputs = ["input/problem.md"] + (
                    [f"results/q{index - 1}/result.json"] if index > 1 else []
                )
                outputs = [
                    f"code/{problem_id}/solve.py",
                    f"results/{problem_id}/result.json",
                    f"reports/{problem_id}_RESULTS.md",
                ]
                if problem_id == "q3":
                    outputs.append("figures/q3/chart.png")
                problems.append({
                    "id": problem_id, "label": f"Problem {index}",
                    "depends_on": dependencies, "method": "enumerate",
                    "inputs": inputs, "outputs": outputs,
                    "validation": ["oracle"], "runtime_limit_seconds": 60,
                })
            plan = {"schema_version": 1, "problems": problems}
            workflow = initial_workflow(
                {"model": "openai/gpt-5.6-sol", "thinking": "high"},
                {"model": "openai/gpt-5.6-luna", "thinking": "high"},
            )
            workflow["stage_snapshot"] = workspace_hashes(workspace)
            (workspace / "project.json").write_text(json.dumps({
                "status": "running", "problem_file": "input/problem.md",
                "competition": "MCM", "language": "English", "paper_engine": "LaTeX",
                "workflow": workflow,
            }))
            (workspace / "reports" / "ANALYSIS_MODELING_REPORT.md").write_text("analysis")
            (workspace / "execution_plan.json").write_text(json.dumps(plan))
            runtime = TaskRuntime("a" * 12, workspace, status="running")
            runtime._begin_current = AsyncMock()  # type: ignore[method-assign]
            runtime.terminate = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()
            for problem in problems:
                problem_id = str(problem["id"])
                code = workspace / "code" / problem_id
                results = workspace / "results" / problem_id
                code.mkdir()
                results.mkdir()
                (code / "solve.py").write_text("print('ok')\n")
                (workspace / "reports" / f"{problem_id}_RESULTS.md").write_text("ok")
                (results / "result.json").write_text(json.dumps({
                    "problem_id": problem_id, "status": "pass", "metrics": [{
                        "name": "objective", "value": 1, "unit": "", "description": "test"
                    }]
                }))
                (results / "verification.json").write_text(json.dumps({
                    "status": "pass", "smoke_runtime_seconds": 0.01,
                    "estimated_runtime_seconds": 0.1, "actual_runtime_seconds": 0.1,
                    "checks": ["oracle"]
                }))
                if problem_id == "q3":
                    (workspace / "figures" / "q3").mkdir()
                    (workspace / "figures" / "q3" / "chart.png").write_bytes(b"png")
                await runtime._settled()

            (workspace / "reports" / "DRAWIO_REPORT.md").write_text("not required")
            await runtime._settled()
            (workspace / "paper" / "main.pdf").write_bytes(b"test pdf")
            with patch.object(bridge, "_paper_readable", return_value=True):
                await runtime._settled()
                (workspace / "reports" / "VERIFY_REPORT.md").write_text("Conclusion: PASS\n")
                await runtime._settled()

            saved = runtime._project()
            self.assertEqual(saved["status"], "completed")
            self.assertTrue(all(
                phase["status"] == "completed" for phase in saved["workflow"]["phases"]
            ))
            self.assertEqual(set(saved["workflow"]["frozen"]), {"q1", "q2", "q3"})
            self.assertEqual(runtime._begin_current.await_count, 6)
            runtime.terminate.assert_awaited_once()

    async def test_problem_success_freezes_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime, _ = self._make_runtime(directory)
            code = runtime.workspace / "code" / "q1"
            results = runtime.workspace / "results" / "q1"
            code.mkdir()
            results.mkdir()
            (code / "solve.py").write_text("print(2000)\n")
            (runtime.workspace / "reports" / "q1_RESULTS.md").write_text("ok")
            (results / "result.json").write_text(json.dumps({
                "problem_id": "q1", "status": "pass", "metrics": [{
                    "name": "profit", "value": 2000, "unit": "yuan", "description": "maximum"
                }]
            }))
            (results / "verification.json").write_text(json.dumps({
                "status": "pass", "smoke_runtime_seconds": 0.01,
                "estimated_runtime_seconds": 0.1, "actual_runtime_seconds": 0.1,
                "checks": ["independent check"]
            }))
            runtime._begin_current = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            saved = runtime._project()["workflow"]
            self.assertEqual(saved["current"], "diagram")
            self.assertIn("q1", saved["frozen"])
            runtime._begin_current.assert_awaited_once()

    async def test_verify_failure_allows_two_bounded_writing_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime, _ = self._make_runtime(directory)
            project = runtime._project()
            project["workflow"]["contract_version"] = 2
            runtime._save_project(project)
            runtime._switch_session = AsyncMock()  # type: ignore[method-assign]
            runtime.prompt = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            for repair_number in (1, 2):
                project = runtime._project()
                workflow = project["workflow"]
                workflow["current"] = "verify"
                workflow["phases"][-1].update(
                    {"status": "running", "attempts": repair_number}
                )
                runtime._save_project(project)

                await runtime._repair_current(
                    [f"validation_failed: visual defect {repair_number}"]
                )

                saved = runtime._project()["workflow"]
                self.assertEqual(saved["current"], "writing")
                self.assertEqual(saved["verify_repair_count"], repair_number)
                self.assertEqual(saved["phases"][-2]["attempts"], repair_number + 1)
                self.assertIn(
                    f"bounded writing repair {repair_number} of 2",
                    runtime.prompt.await_args.args[0],
                )

            project = runtime._project()
            project["workflow"]["current"] = "verify"
            runtime._save_project(project)
            await runtime._repair_current(["validation_failed: visual defect remains"])
            self.assertEqual(runtime.status, "failed")
            self.assertEqual(runtime._project()["workflow"]["verify_repair_count"], 2)

    async def test_final_repair_failure_waits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime, _ = self._make_runtime(directory)
            project = runtime._project()
            project["workflow"]["mode"] = "final_repair"
            project["workflow"]["phases"][1]["attempts"] = 3
            runtime._save_project(project)
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._settled()

            saved = runtime._project()
            self.assertEqual(saved["status"], "waiting")
            self.assertEqual(saved["workflow"]["phases"][1]["status"], "waiting")


class TaskRuntimeTest(unittest.IsolatedAsyncioTestCase):
    """Verify persisted message upserts without a live browser."""

    async def test_runtime_reload_preserves_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "b" * 12
            workspace = Path(directory) / task_id
            workspace.mkdir()
            (workspace / "project.json").write_text(json.dumps({
                "status": "completed", "started_at": "2026-01-02T03:04:05+00:00",
                "model": "openai/gpt-5.6-sol", "thinking": "high",
            }))
            TASKS.clear()
            with patch.object(bridge, "WORKSPACES", Path(directory)):
                runtime = bridge._runtime(task_id)
            self.assertEqual(runtime.started_at, "2026-01-02T03:04:05+00:00")
            TASKS.clear()

    async def test_message_snapshot_retries_transient_windows_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = TaskRuntime("a" * 12, Path(directory))
            real_replace = os.replace
            attempts = 0

            def flaky_replace(source: str | Path, target: str | Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("transient scanner lock")
                real_replace(source, target)

            with patch("os.replace", side_effect=flaky_replace):
                runtime._write_messages("[]")

            self.assertEqual(attempts, 2)
            self.assertEqual(runtime.message_file.read_text(encoding="utf-8"), "[]")

    async def test_failed_bridge_with_running_phase_recovers_as_paused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "e" * 12
            workspace = Path(directory) / task_id
            workspace.mkdir()
            (workspace / "project.json").write_text(json.dumps({
                "status": "failed", "workflow": {
                    "contract_version": 2, "current": "paper_planning",
                    "phases": [{
                        "id": "paper_planning", "status": "running", "last_error": ""
                    }],
                },
            }))
            TASKS.clear()
            with patch.object(bridge, "WORKSPACES", Path(directory)):
                runtime = bridge._runtime(task_id)
            project = json.loads((workspace / "project.json").read_text())
            self.assertEqual(runtime.status, "paused")
            self.assertEqual(project["pause_reason"], "bridge_error_recovery")
            self.assertEqual(project["workflow"]["phases"][0]["status"], "paused")
            TASKS.clear()

    async def test_v3_host_boundary_requires_ledger(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows Host boundary only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "project.json").write_text(json.dumps({
                "status": "running",
                "workflow": {"contract_version": 3},
            }), encoding="utf-8")
            runtime = TaskRuntime(uuid.uuid4().hex[:12], workspace)
            with self.assertRaisesRegex(RuntimeError, "requires project.json and planning/ledger.json"):
                runtime._acquire_host_state()

    async def test_host_state_lock_blocks_same_user_file_mutation(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows mandatory sharing locks only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_path = workspace / "project.json"
            ledger_path = workspace / "planning" / "ledger.json"
            ledger_path.parent.mkdir()
            project_path.write_text(json.dumps({
                "status": "running",
                "workflow": {"contract_version": 3},
            }), encoding="utf-8")
            ledger_path.write_text(json.dumps({
                "schema_version": 1,
                "inventory": {},
                "problems": {},
                "plan_version": 0,
            }), encoding="utf-8")
            runtime = TaskRuntime(uuid.uuid4().hex[:12], workspace)

            runtime._acquire_host_state()
            try:
                with self.assertRaises(PermissionError):
                    project_path.write_text("forged", encoding="utf-8")
                with self.assertRaises(PermissionError):
                    ledger_path.write_text("forged", encoding="utf-8")
                project = runtime._project()
                project["status"] = "paused"
                runtime._save_project(project)
                self.assertEqual(runtime._project()["status"], "paused")
            finally:
                runtime._release_host_state()
            project_path.write_text("{}", encoding="utf-8")
            self.assertEqual(project_path.read_text(encoding="utf-8"), "{}")

    async def test_host_journal_recovers_from_partial_latest_write(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows Host journal only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_path = workspace / "project.json"
            ledger_path = workspace / "planning" / "ledger.json"
            ledger_path.parent.mkdir()
            project_path.write_text(json.dumps({
                "status": "running",
                "workflow": {"contract_version": 3},
            }), encoding="utf-8")
            ledger_path.write_text(json.dumps({
                "schema_version": 1,
                "inventory": {},
                "problems": {},
                "plan_version": 0,
            }), encoding="utf-8")
            runtime = TaskRuntime(uuid.uuid4().hex[:12], workspace)
            runtime._acquire_host_state()
            paused = runtime._project()
            paused["status"] = "paused"
            runtime._save_project(paused)
            running = runtime._project()
            running["status"] = "running"
            runtime._save_project(running)
            assert runtime._host_boundary
            latest_slot = runtime._host_boundary.journal_paths[
                runtime._host_boundary.generation % 2
            ]
            runtime._host_boundary._write_handle(latest_slot, b"{")
            runtime._host_boundary._write_handle(project_path, b"{")
            runtime._release_host_state()

            bridge.WindowsHostBoundary.recover(runtime.task_id, workspace)

            recovered = json.loads(project_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["status"], "paused")

    async def test_resume_mirror_is_checkpointed_not_rolled_back(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows Host journal only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_path = workspace / "project.json"
            ledger_path = workspace / "planning" / "ledger.json"
            ledger_path.parent.mkdir()
            project_path.write_text(json.dumps({
                "status": "running",
                "workflow": {"contract_version": 3},
            }), encoding="utf-8")
            ledger_path.write_text(json.dumps({
                "schema_version": 1,
                "inventory": {},
                "problems": {},
                "plan_version": 0,
            }), encoding="utf-8")
            task_id = uuid.uuid4().hex[:12]
            first = TaskRuntime(task_id, workspace)
            first._acquire_host_state()
            paused = first._project()
            paused["status"] = "paused"
            first._save_project(paused)
            assert first._host_boundary
            paused_generation = first._host_boundary.generation
            first._release_host_state()

            resumed = json.loads(project_path.read_text(encoding="utf-8"))
            resumed["status"] = "starting"
            resumed["resume_count"] = 1
            project_path.write_text(json.dumps(resumed), encoding="utf-8")
            second = TaskRuntime(task_id, workspace)
            second._acquire_host_state()
            try:
                checkpointed = second._project()
                self.assertEqual(checkpointed["status"], "starting")
                self.assertEqual(checkpointed["resume_count"], 1)
                assert second._host_boundary
                self.assertGreater(
                    second._host_boundary.generation, paused_generation
                )
            finally:
                second._release_host_state()

    async def test_preassignment_failure_kills_suspended_process_before_unlock(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows Job Objects only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_path = workspace / "project.json"
            ledger_path = workspace / "planning" / "ledger.json"
            ledger_path.parent.mkdir()
            project_path.write_text(json.dumps({
                "status": "running",
                "workflow": {"contract_version": 3},
            }), encoding="utf-8")
            ledger_path.write_text(json.dumps({
                "schema_version": 1,
                "inventory": {},
                "problems": {},
                "plan_version": 0,
            }), encoding="utf-8")
            runtime = TaskRuntime(uuid.uuid4().hex[:12], workspace)
            runtime._acquire_host_state()
            runtime.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                creationflags=bridge.CREATE_NO_WINDOW | bridge.CREATE_SUSPENDED,
            )
            assert runtime._host_boundary
            empty_job = runtime._host_boundary.kernel.CreateJobObjectW(None, None)
            self.assertTrue(empty_job)
            runtime._host_boundary.job_handle = int(empty_job)
            runtime._host_boundary.job_assigned = False

            await runtime.terminate()

            self.assertIsNotNone(runtime.process.returncode)
            project_path.write_text("{}", encoding="utf-8")

    async def test_windows_job_kills_descendants_before_unlock(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows Job Objects only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            project_path = workspace / "project.json"
            ledger_path = workspace / "planning" / "ledger.json"
            ledger_path.parent.mkdir()
            project_path.write_text(json.dumps({
                "status": "running",
                "workflow": {"contract_version": 3},
            }), encoding="utf-8")
            ledger_path.write_text(json.dumps({
                "schema_version": 1,
                "inventory": {},
                "problems": {},
                "plan_version": 0,
            }), encoding="utf-8")
            runtime = TaskRuntime(uuid.uuid4().hex[:12], workspace)
            runtime._acquire_host_state()
            child_pid_file = workspace / "child.pid"
            script = (
                "import pathlib,subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
                "time.sleep(60)"
            )
            runtime.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                creationflags=bridge.CREATE_NO_WINDOW | bridge.CREATE_SUSPENDED,
            )
            assert runtime._host_boundary
            runtime._host_boundary.assign_and_resume(runtime.process.pid)
            for _ in range(100):
                if child_pid_file.is_file():
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(child_pid_file.is_file())
            child_pid = int(child_pid_file.read_text())
            await runtime.terminate()
            self.assertFalse(bridge._pid_exists(child_pid))
            project_path.write_text("{}", encoding="utf-8")

    async def test_rpc_stream_limit_accepts_large_jsonl_event(self) -> None:
        reader = asyncio.StreamReader(limit=RPC_STREAM_LIMIT_BYTES)
        payload = b'{"type":"event","text":"' + b"x" * 100_000 + b'"}\n'
        reader.feed_data(payload)
        reader.feed_eof()

        self.assertEqual(await reader.readline(), payload)

    async def test_rpc_command_matches_response_by_command(self) -> None:
        class FakeStdin:
            def __init__(self) -> None:
                self.data = b""

            def write(self, value: bytes) -> None:
                self.data += value

            async def drain(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            runtime = TaskRuntime("a" * 12, Path(directory))
            stdin = FakeStdin()
            runtime.process = SimpleNamespace(stdin=stdin)  # type: ignore[assignment]
            pending = asyncio.create_task(runtime.rpc_command({"type": "new_session"}))
            await asyncio.sleep(0)
            await runtime._handle_event({
                "type": "response", "command": "new_session", "success": True,
                "data": {"cancelled": False},
            })

            response = await pending
            self.assertTrue(response["success"])
            self.assertIn(b'"type": "new_session"', stdin.data)

    async def test_switch_session_uses_frozen_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "project.json").write_text(json.dumps({
                "workflow": {"profiles": {"planner": {
                    "model": "openai/gpt-5.6-sol", "thinking": "high"
                }}}
            }), encoding="utf-8")
            runtime = TaskRuntime("a" * 12, workspace)
            runtime.rpc_command = AsyncMock(return_value={"success": True})  # type: ignore[method-assign]

            await runtime._switch_session("planner")

            self.assertEqual(
                [call.args[0]["type"] for call in runtime.rpc_command.await_args_list],
                ["new_session", "set_model", "set_thinking_level", "prompt"],
            )
            policy = runtime.rpc_command.await_args_list[-1].args[0]
            self.assertEqual(
                policy["message"],
                f"/mathmodel-tool-policy {runtime._tool_policy_token} work",
            )
            self.assertEqual(runtime.model, "openai/gpt-5.6-sol")
            self.assertEqual(runtime.thinking_level, "high")

    async def test_reviewer_session_switches_to_read_only_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "project.json").write_text(json.dumps({
                "workflow": {
                    "mode": "method_audit",
                    "current": "method_audit:q1",
                    "profiles": {"planner": {
                        "model": "openai/gpt-5.6-sol", "thinking": "high"
                    }},
                }
            }), encoding="utf-8")
            runtime = TaskRuntime("a" * 12, workspace)
            runtime.rpc_command = AsyncMock(return_value={"success": True})  # type: ignore[method-assign]

            await runtime._switch_session("planner")

            policy = runtime.rpc_command.await_args_list[-1].args[0]
            self.assertEqual(policy["type"], "prompt")
            self.assertEqual(
                policy["message"],
                f"/mathmodel-tool-policy {runtime._tool_policy_token} review",
            )

    async def test_watchdog_aborts_only_current_agent_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = TaskRuntime("a" * 12, Path(directory))
            runtime._tool_watchdogs["tool"] = asyncio.current_task()  # type: ignore[assignment]
            runtime.send_rpc = AsyncMock()  # type: ignore[method-assign]
            runtime.system = AsyncMock()  # type: ignore[method-assign]

            await runtime._watch_tool("tool", 0)

            self.assertTrue(runtime._budget_exceeded)
            runtime.send_rpc.assert_awaited_once_with({"type": "abort"})

    async def test_set_status_persists_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "project.json").write_text(
                '{"status":"ready"}', encoding="utf-8"
            )
            runtime = TaskRuntime("a" * 12, workspace, status="ready")

            runtime.set_status("cancelled")

            self.assertEqual(runtime.status, "cancelled")
            self.assertIn(
                '"status": "cancelled"',
                (workspace / "project.json").read_text(encoding="utf-8"),
            )

    async def test_settled_preserves_cancelled_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = TaskRuntime("a" * 12, Path(directory), status="cancelled")
            await runtime._settled()

            self.assertEqual(runtime.status, "cancelled")
            self.assertEqual(runtime.messages, [])

    async def test_publish_upserts_by_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = TaskRuntime("a" * 12, Path(directory))
            await runtime.publish({"id": "one", "content": "first"})
            await runtime.publish({"id": "one", "content": "updated"})

            self.assertEqual(runtime.messages, [{"id": "one", "content": "updated"}])
            self.assertIn("updated", runtime.message_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

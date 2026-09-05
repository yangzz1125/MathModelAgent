"""The explicit continuation command never restarts an unaccepted source."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import continue_paper


class PaperContinuationTest(unittest.TestCase):
    def test_rejects_active_or_pre_scientific_source_without_creating_destination(self):
        for status, stage in (("running", "paper_planning"), ("failed", "problem:q1")):
            with self.subTest(status=status, stage=stage), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source"
                source.mkdir()
                original = json.dumps({"status": status, "workflow": {
                    "contract_version": 3, "current": stage,
                }})
                (source / "project.json").write_text(original)
                destination = Path(directory) / "continued"
                with self.assertRaises(ValueError):
                    continue_paper.prepare(source, destination)
                self.assertFalse(destination.exists())
                self.assertEqual((source / "project.json").read_text(), original)

    def test_import_preserves_requirements_but_never_imports_old_manuscript(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            for name in ("input", "planning", "code", "results", "figures", "reports", "paper"):
                (source / name).mkdir(parents=True)
            for relative in ("planning/ledger.json", "input_manifest.json", "execution_plan.json", "reports/PLAN_COMPLETENESS.json"):
                (source / relative).write_text("{}")
            (source / "input/user_requirements_1.md").write_text("Exact results required")
            (source / "paper/main.tex").write_text("Must not be imported")
            (source / "project.json").write_text(json.dumps({
                "status": "failed", "user_notes": "Exact results required",
                "user_requirements_file": "input/user_requirements_1.md",
                "workflow": {"contract_version": 3, "current": "writing", "frozen": {"q1": {}},
                             "phases": [{"id": "paper_planning", "label": "Paper Planning"}]},
            }))
            before = (source / "project.json").read_bytes()
            destination = Path(directory) / "continued"
            with patch.object(continue_paper, "validate_execution_plan", return_value={"problems": [{"id": "q1"}]}), patch.object(continue_paper, "frozen_errors", return_value=[]), patch.object(continue_paper, "acceptance_chain_errors", return_value=[]):
                project = continue_paper.prepare(source, destination)
            self.assertEqual(project["user_notes"], "Exact results required")
            self.assertTrue((destination / project["user_requirements_file"]).is_file())
            self.assertEqual(list((destination / "paper").iterdir()), [])
            self.assertEqual((source / "project.json").read_bytes(), before)
            self.assertEqual(project["status"], "paused")

    def test_changed_frozen_evidence_blocks_import(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "planning").mkdir(parents=True)
            (source / "planning/ledger.json").write_text("{}")
            (source / "project.json").write_text(json.dumps({"status": "failed", "workflow": {
                "contract_version": 3, "current": "paper_planning", "frozen": {"q1": {}},
            }}))
            destination = Path(directory) / "continued"
            with (
                patch.object(continue_paper, "validate_execution_plan", return_value={"problems": [{"id": "q1"}]}),
                patch.object(continue_paper, "frozen_errors", return_value=["artifact_changed: frozen evidence"]),
                patch.object(continue_paper, "acceptance_chain_errors", return_value=[]),
                self.assertRaisesRegex(ValueError, "artifact_changed"),
            ):
                continue_paper.prepare(source, destination)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()

"""Regression checks for paper build directories and reachable source validation."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pi.scientific_review import paper_source_errors
from pi.staged_workflow import (
    PAPER_BUILD_CONTRACT, final_stage_prompt, paper_manifest_repair_prompt,
    repair_prompt, stage_scope_errors, workspace_hashes, writing_repair_prompt,
)


class PaperBuildContractTest(unittest.TestCase):
    def test_all_writing_entries_include_the_same_build_contract(self):
        prompts = [
            final_stage_prompt("writing", competition="CUMCM", language="Chinese", paper_engine="LaTeX"),
            paper_manifest_repair_prompt(["missing PDF"]),
            writing_repair_prompt(["compilation failed"]),
            repair_prompt("writing", ["missing PDF"]),
        ]
        for prompt in prompts:
            self.assertIn(PAPER_BUILD_CONTRACT, prompt)
        self.assertNotIn(PAPER_BUILD_CONTRACT, repair_prompt("diagram", []))

    def test_strict_references_ignore_unused_sources_but_reject_reachable_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper"
            (paper / "sections").mkdir(parents=True)
            main = paper / "main.tex"
            main.write_text("\\input{sections/body}\n\\input{references}\n")
            body = paper / "sections/body.tex"
            body.write_text("\\cite{current}\n% \\input{missing}\n")
            (paper / "references.tex").write_text("\\bibitem{current} Book.\n")
            (paper / "sections/unused.tex").write_text("\\cite{obsolete}\n\\tableofcontents\n\\tableofcontents")
            self.assertEqual(paper_source_errors(root, strict=True), [])
            self.assertTrue(any("obsolete" in error for error in paper_source_errors(root)))
            body.write_text("\\input{nested}\n")
            (paper / "sections/nested.tex").write_text("\\cite{current,missing}\n\\input{body}\n")
            self.assertEqual(paper_source_errors(root, strict=True), [
                "paper_references: citations without bibitems: ['missing']",
            ])
            body.write_text("\\cite{current}\n\\input{absent}\n")
            self.assertTrue(any("unresolved literal" in error for error in paper_source_errors(root, strict=True)))
            (root / "outside.tex").write_text("untrusted")
            body.write_text("\\cite{current}\n\\input{../outside}\n")
            self.assertTrue(any(error.startswith("paper_sources:") for error in paper_source_errors(root, strict=True)))

    @unittest.skipUnless(shutil.which("bash") and shutil.which("xelatex"), "bash/XeLaTeX required")
    def test_exact_prompt_command_compiles_inside_paper_and_fails_before_missing_input(self):
        command = next(line for line in PAPER_BUILD_CONTRACT.splitlines() if line.startswith("(cd paper && test -f main.tex"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper"
            paper.mkdir()
            failed = subprocess.run([shutil.which("bash"), "-c", command], cwd=root, capture_output=True, timeout=30)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(list(root.rglob("*.log")))
            (paper / "main.tex").write_text("\\documentclass{article}\n\\begin{document}Build check.\\end{document}\n")
            before = workspace_hashes(root)
            result = subprocess.run([shutil.which("bash"), "-c", command], cwd=root, capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace")[-3000:])
            self.assertEqual(result.stdout.count(b"Output written on"), 2)
            self.assertTrue((paper / "main.pdf").read_bytes().startswith(b"%PDF-"))
            self.assertEqual(stage_scope_errors(root, before, "writing"), [])
            (root / "texput.log").write_text("unexpected root output")
            self.assertIn("artifact_changed: stage wrote outside its boundary: texput.log", stage_scope_errors(root, before, "writing"))


if __name__ == "__main__":
    unittest.main()

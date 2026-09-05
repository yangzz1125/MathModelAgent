"""Shared layout checks and a real two-pass title/contents/float smoke test."""

import shutil
import subprocess
import tempfile
import unittest
import re
from pathlib import Path

from pi.paper_layout import LAYOUT_SOURCE, LAYOUT_SOURCES, LAYOUT_VERSION, paper_layout_errors, paper_layout_policy
from pi.scientific_review import paper_source_errors
from pi.staged_workflow import PAPER_LAYOUT_CONTRACT, final_stage_prompt, paper_manifest_repair_prompt, repair_prompt, writing_repair_prompt

MASTER = r"""\documentclass[a4paper,12pt]{ctexart}
\input{cumcm-layout.tex}
\begin{document}
\papertitle{Layout regression}
\begin{abstract}A compact title, separated contents and freely placed floats.\end{abstract}
\papercontents
\section{Results}
Verified result text.
\begin{figure}[htbp]
\centering\rule{0.8\textwidth}{45mm}
\caption{Layout test rectangle, not a scientific figure.}
\end{figure}
Text continues beside the float placement decision.
\end{document}
"""


class PaperLayoutTest(unittest.TestCase):
    def test_policy_is_limited_and_all_writing_routes_share_instructions(self):
        project = {"competition": "CUMCM", "language": "Chinese", "paper_engine": "LaTeX"}
        self.assertEqual(paper_layout_policy(project), LAYOUT_VERSION)
        for field, value in (("competition", "MCM"), ("language", "English"), ("paper_engine", "Typst")):
            self.assertIsNone(paper_layout_policy({**project, field: value}))
        for prompt in (final_stage_prompt("writing", **project), paper_manifest_repair_prompt([]), writing_repair_prompt([]), repair_prompt("writing", [])):
            self.assertIn(PAPER_LAYOUT_CONTRACT, prompt)

    def test_missing_changed_or_unwired_layout_is_rejected_but_legacy_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper"
            paper.mkdir()
            main = paper / "main.tex"
            main.write_text(MASTER)
            self.assertEqual(paper_layout_errors(root, None), [])
            self.assertTrue(paper_layout_errors(root, LAYOUT_VERSION))
            style = paper / "cumcm-layout.tex"
            shutil.copyfile(LAYOUT_SOURCE, style)
            self.assertEqual(paper_layout_errors(root, LAYOUT_VERSION), [])
            self.assertEqual(paper_source_errors(root, strict=True, legacy_visual=False), [])
            for old, new in (("12pt", "11pt"), (r"\input{cumcm-layout.tex}", "% omitted"), (r"\papertitle{Layout regression}", r"\maketitle"), (r"\papercontents", r"\tableofcontents")):
                main.write_text(MASTER.replace(old, new))
                self.assertTrue(paper_layout_errors(root, LAYOUT_VERSION), old)
            main.write_text(MASTER)
            style.write_text("% replaced layout")
            self.assertTrue(paper_layout_errors(root, LAYOUT_VERSION))
            self.assertTrue(paper_layout_errors(root, "unknown"))
            shutil.copyfile(LAYOUT_SOURCES["cumcm-v1"], style)
            self.assertEqual(paper_layout_errors(root, "cumcm-v1"), [])

    @unittest.skipUnless(shutil.which("xelatex"), "XeLaTeX required")
    def test_compiled_abstract_uses_full_width_body_size_and_two_character_indent(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory)
            content = MASTER.replace(r"\begin{document}", r"\makeatletter\begin{document}")
            content = content.replace("A compact title, separated contents and freely placed floats.", r"\typeout{ABSTRACTWIDTH=\the\linewidth; TEXTWIDTH=\the\textwidth; INDENT=\the\parindent; SIZE=\f@size}" + "AbstractFirst " + "sample text " * 65)
            (paper / "main.tex").write_text(content)
            shutil.copyfile(LAYOUT_SOURCE, paper / "cumcm-layout.tex")
            result = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=paper, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0)
            log = (paper / "main.log").read_text(errors="replace")
            values = re.search(r"ABSTRACTWIDTH=([\d.]+)pt; TEXTWIDTH=([\d.]+)pt; INDENT=([\d.]+)pt;\s*SIZE=([\d.]+)", log)
            self.assertIsNotNone(values, log[-2000:])
            width, text_width, indent, size = map(float, values.groups())
            self.assertAlmostEqual(width, text_width)
            self.assertAlmostEqual(indent, 24, delta=0.1)
            self.assertEqual(size, 12)

    @unittest.skipUnless(shutil.which("xelatex"), "XeLaTeX required")
    def test_shared_layout_compiles_without_blank_pages_or_box_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory)
            (paper / "main.tex").write_text(MASTER)
            shutil.copyfile(LAYOUT_SOURCE, paper / "cumcm-layout.tex")
            for _ in range(2):
                result = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=paper, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace")[-2000:])
            log = (paper / "main.log").read_text(errors="replace")
            self.assertNotIn("Overfull", log)
            self.assertIn("(3 pages)", log)
            self.assertTrue((paper / "main.pdf").read_bytes().startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()

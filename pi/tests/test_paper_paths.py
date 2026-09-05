"""Canonical Host paths must preserve evidence and reject outside-workspace PDFs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from pi.paper_evidence import paper_visual_errors, render_paper_pages


class PaperPathTests(unittest.TestCase):
    def test_relative_workspace_and_resolved_pdf_share_one_evidence_root(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix='path-alias-') as directory:
            root = Path(directory).resolve()
            paper = root / 'paper'
            paper.mkdir()
            pdf = paper / 'main.pdf'
            pdf.write_bytes(b'unit fixture; external renderer is mocked')
            relative_root = Path(root.name)
            def renderer(command, timeout, cancelled):
                if command[0] == 'pdfinfo':
                    self.assertEqual(Path(command[1]), pdf)
                    return b'Pages: 1\n'
                self.assertEqual(command[0], 'pdftoppm')
                self.assertEqual(Path(command[-2]), pdf)
                image = Image.new('RGB', (900, 900), 'white')
                ImageDraw.Draw(image).rectangle((50, 50, 700, 100), fill='black')
                image.save(command[-1] + '-1.png')
                return b''
            with patch('pi.paper_evidence.shutil.which', side_effect=lambda name: name), patch('pi.paper_evidence._run', side_effect=renderer):
                record = render_paper_pages(relative_root, pdf)
            self.assertEqual(record['pdf'], 'paper/main.pdf')
            self.assertEqual(record['page_count'], 1)
            self.assertEqual(set(record['files']), {'paper/main.pdf', 'paper/rendered_pages/page-01.png', 'paper/rendered_pages/page-01-gray.png'})
            self.assertEqual(paper_visual_errors(relative_root, record), [])

    def test_outside_pdf_is_rejected_before_any_tool_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'workspace'
            root.mkdir()
            outside = base / 'outside.pdf'
            outside.write_bytes(b'not authorized input')
            with patch('pi.paper_evidence._run') as tool:
                with self.assertRaises(ValueError):
                    render_paper_pages(root, outside)
                tool.assert_not_called()

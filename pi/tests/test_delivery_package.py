import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pi.delivery_package import DeliveryPackageError, build_delivery_package


class DeliveryPackageTest(unittest.TestCase):
    def test_packages_only_accepted_user_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspaces" / "abc123def456"
            files = {
                "paper/main.pdf": b"%PDF-1.4\naccepted",
                "paper/main.tex": b"paper source",
                "paper/main.log": b"compiler noise",
                "code/q1/solve.py": b"print(1)\n",
                "code/q1/__pycache__/solve.pyc": b"cache",
                "input/problem.md": b"problem",
                "input_manifest.json": b"{}",
                "input/.env": b"API_KEY=secret",
                "results/q1/result.json": b"{}",
                "figures/q1/chart.pdf": b"figure",
                ".pi-bridge/messages.json": b"private",
            }
            for relative, content in files.items():
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            digest = hashlib.sha256(files["paper/main.pdf"]).hexdigest()
            project = {
                "project_id": "abc123def456",
                "status": "completed",
                "workflow": {
                    "document_review": {"verdict": "accept"},
                    "paper_visual_evidence": {
                        "pdf": "paper/main.pdf",
                        "files": {"paper/main.pdf": digest},
                    },
                },
            }
            (workspace / "project.json").write_text(
                json.dumps(project), encoding="utf-8"
            )
            archive = Path(directory) / "delivery.zip"

            manifest = build_delivery_package(
                workspace, "abc123def456", archive
            )

            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                required = {
                    "README.md",
                    "manifest.json",
                    "reports/",
                    "paper/main.pdf",
                    "paper/main.tex",
                    "code/q1/solve.py",
                    "input/problem.md",
                    "input_manifest.json",
                    "results/q1/result.json",
                    "figures/q1/chart.pdf",
                }
                self.assertTrue(required.issubset(names))
                self.assertFalse(
                    any(
                        ".env" in name
                        or ".pi" in name
                        or "__pycache__" in name
                        or name.endswith(".log")
                        for name in names
                    )
                )
                packaged = json.loads(bundle.read("manifest.json"))
            self.assertEqual(manifest, packaged)
            self.assertEqual(packaged["paper_acceptance"]["verdict"], "accept")

    def test_rejects_unaccepted_paper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "abc123def456"
            workspace.mkdir()
            (workspace / "project.json").write_text(
                json.dumps(
                    {
                        "project_id": "abc123def456",
                        "status": "partial",
                        "workflow": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DeliveryPackageError, "尚未完成"):
                build_delivery_package(
                    workspace,
                    "abc123def456",
                    Path(directory) / "delivery.zip",
                )


if __name__ == "__main__":
    unittest.main()

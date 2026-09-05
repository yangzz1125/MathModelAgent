"""History discovery must survive Bridge restarts without touching tasks."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi import bridge


class ProjectHistoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_history_discovers_disk_tasks_and_skips_corrupt_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = {
                "a" * 12: {"status": "failed", "source_folder": "older", "created_at": "2026-09-01T00:00:00+00:00"},
                "b" * 12: {"status": "running", "source_folder": "newer", "created_at": "2026-09-02T00:00:00+00:00", "workflow": {"current": "writing"}, "continuation_source": {"project_id": "a" * 12}},
                "invalid-id": {"status": "completed"},
            }
            for task_id, record in records.items():
                path = root / task_id / "project.json"
                path.parent.mkdir()
                path.write_text(json.dumps(record), encoding="utf-8")
            corrupt = root / ("c" * 12) / "project.json"
            corrupt.parent.mkdir()
            corrupt.write_text("{invalid")
            before = {path: path.read_bytes() for path in root.glob("*/project.json")}
            with patch.object(bridge, "WORKSPACES", root), patch.object(bridge, "_runtime") as runtime:
                rows = await bridge.list_projects()
            self.assertTrue(any(route.path == "/projects" and "GET" in route.methods for route in bridge.app.routes))
            self.assertEqual([row["task_id"] for row in rows], ["b" * 12, "a" * 12])
            self.assertEqual(rows[0]["current_stage"], "writing")
            self.assertEqual(rows[0]["continued_from"], "a" * 12)
            self.assertEqual(rows[1]["status"], "failed")
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            runtime.assert_not_called()

    async def test_empty_history_is_a_successful_empty_list(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, "WORKSPACES", Path(directory)):
            self.assertEqual(await bridge.list_projects(), [])


if __name__ == "__main__":
    unittest.main()

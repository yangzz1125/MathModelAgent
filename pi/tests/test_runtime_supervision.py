"""Fault injection for mixin integration. Harness is not the original bridge."""
from __future__ import annotations
import asyncio
import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from pi.runtime_support import BudgetLedger, RuntimePolicy, TurnLease
from pi.runtime_supervision import RuntimeState, RuntimeSupervisionMixin, _write_partial_delivery


class Writer:
    def __init__(self, delay=0):
        self.delay = delay
        self.payloads = []
        self.callback = None
    def write(self, raw):
        payload = json.loads(raw)
        self.payloads.append(payload)
        if self.callback:
            self.callback(payload)
    async def drain(self):
        await asyncio.sleep(self.delay)


class Harness(RuntimeSupervisionMixin):
    def __init__(self, workspace, policy=None):
        self.workspace = Path(workspace)
        self.project = {"status": "running", "workflow": {"current": "problem:1", "mode": "execute", "phases": [{"id": "problem:1", "status": "running"}]}}
        self.status = "running"
        self.clients = set()
        self.messages = []
        self._write_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._transition_lock = asyncio.Lock()
        self._terminate_lock = asyncio.Lock()
        self._transitions = set()
        self._pending_rpc = {}
        self._prompt_watchdogs = {}
        self._host_render_cancel = None
        self._stopping = False
        self._last_assistant_text = ""
        self.process = SimpleNamespace(stdin=Writer(), returncode=None, stdout=asyncio.StreamReader())
        self.runner = None
        self._runtime_safety_state = RuntimeState(policy or RuntimePolicy(), BudgetLedger())
        self._runtime_safety_state.started = time.monotonic()
        self.settled_count = 0
        self.pause_count = 0
        self.resume_count = 0
        self.terminated = False
        self.core_events = []
        self.snapshots = []
        self.save_calls = 0
    def _project(self):
        return copy.deepcopy(self.project)
    def _save_project_core(self, project):
        self.save_calls += 1
        self.project = copy.deepcopy(project)
    def set_status(self, status):
        self.status = status
        project = self._project()
        project["status"] = status
        self._save_project(project)
    def _write_messages(self, snapshot):
        self.snapshots.append(json.loads(snapshot))
    def _reviewer_capability(self, workflow):
        return workflow.get("mode") == "scientific_review"
    def _current_runtime_limit(self):
        return None
    @staticmethod
    def _current_phase(workflow):
        return next((p for p in workflow.get("phases", []) if p["id"] == workflow.get("current")), None)
    async def _handle_event_core(self, event):
        self.core_events.append(event)
        if event.get("type") == "response":
            future = self._pending_rpc.pop(event.get("id"), None)
            if future and not future.done():
                future.set_result(event)
        if event.get("type") == "message_end":
            self._last_assistant_text = event.get("message", {}).get("text", "")
    async def _settled(self):
        self.settled_count += 1
    async def _wait_with_errors_core(self, errors):
        self.core_errors = errors
        self.set_status("failed")
    async def _terminate_process_core(self):
        self.terminated = True
        if self.process:
            self.process.returncode = 0
    async def _stop_runtime(self):
        await self._quiesce_transitions()
        await self._terminate_process()
        self.process = None
    async def _pause(self, reason):
        self.pause_count += 1
        self._stopping = True
        await self._quiesce_transitions()
        self.set_status("paused")
        await self._stop_runtime()
    async def _resume(self):
        assert self.terminated and self.process is None, "old writer is still alive"
        assert not self._safety_state().cleanup_required
        self.resume_count += 1
        self._stopping = False
        self.set_status("running")
        self._safety_state().epoch += 1
        self.process = SimpleNamespace(stdin=Writer(), returncode=None, stdout=asyncio.StreamReader())
    async def _run_core(self, prompt):
        await self.prompt(prompt)


class SupervisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = Harness(self.temp.name)

    async def asyncTearDown(self):
        state = self.host._safety_state()
        self.host._stopping = True
        tasks = [t for t in [state.monitor, state.recovery, state.log_task] if t and not t.done()]
        tasks += [t for t in self.host._transitions if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if state.fanout:
            await state.fanout.close()
        self.temp.cleanup()

    async def test_ack_without_settled_times_out_and_recovers(self):
        h = self.host
        s = h._safety_state()
        s.policy = RuntimePolicy(idle_seconds=0.015, ack_seconds=0.1, poll_seconds=0.002)
        await h.prompt("solve")
        await h._handle_event({"type": "response", "id": s.turn.request_id, "success": True})
        s.monitor = asyncio.create_task(h._monitor_runtime(s.epoch))
        await asyncio.wait_for(s.monitor, 0.3)
        await asyncio.wait_for(s.recovery, 0.3)
        self.assertEqual((h.pause_count, h.resume_count), (1, 1))
        self.assertEqual(s.ledger.restarts, 1)
        self.assertIn("agent_idle_timeout", s.ledger.faults[-1]["reason"])

    async def test_ack_timeout_is_detected(self):
        h = self.host
        s = h._safety_state()
        s.policy = RuntimePolicy(ack_seconds=0.01, poll_seconds=0.002)
        await h.prompt("solve")
        s.monitor = asyncio.create_task(h._monitor_runtime(s.epoch))
        await asyncio.wait_for(s.monitor, 0.3)
        await asyncio.wait_for(s.recovery, 0.3)
        self.assertIn("prompt_ack_timeout", s.ledger.faults[-1]["reason"])

    async def test_duplicate_settled_advances_once(self):
        await self.host.prompt("solve")
        await self.host._handle_event({"type": "agent_settled"})
        await self.host._handle_event({"type": "agent_settled"})
        await asyncio.sleep(0)
        self.assertEqual(self.host.settled_count, 1)

    async def test_agent_end_does_not_advance(self):
        await self.host.prompt("solve")
        await self.host._handle_event({"type": "agent_end"})
        await asyncio.sleep(0)
        self.assertEqual(self.host.settled_count, 0)
        self.assertFalse(self.host._safety_state().turn.settled)

    async def test_no_duplicate_prompt_or_steering(self):
        await self.host.prompt("one")
        with self.assertRaisesRegex(RuntimeError, "already active"):
            await self.host.prompt("two")
        self.assertEqual(len(self.host.process.stdin.payloads), 1)
        self.assertNotIn("streamingBehavior", self.host.process.stdin.payloads[0])

    async def test_rpc_write_deadline_and_pending_cleanup(self):
        h = self.host
        h._safety_state().policy = RuntimePolicy(write_seconds=0.015)
        h.process.stdin.delay = 10
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            await h.rpc_command({"type": "get_state"}, timeout=0.5)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertFalse(h._pending_rpc)

    async def test_rpc_total_deadline_includes_drain(self):
        h = self.host
        h.process.stdin.delay = 10
        with self.assertRaises(TimeoutError):
            await h.rpc_command({"type": "get_state"}, timeout=0.015)
        self.assertFalse(h._pending_rpc)

    async def test_rpc_writer_lock_is_bounded(self):
        h = self.host
        s = h._safety_state()
        s.policy = RuntimePolicy(write_seconds=0.01)
        await s.send_lock.acquire()
        try:
            with self.assertRaises(TimeoutError):
                await h.send_rpc({"type": "get_state"})
        finally:
            s.send_lock.release()

    async def test_rpc_cancelled_clears_future(self):
        task = asyncio.create_task(self.host.rpc_command({"type": "get_state"}))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.host._pending_rpc)

    async def test_cancelled_new_session_is_not_success(self):
        h = self.host
        def reply(payload):
            asyncio.create_task(h._handle_event({"type": "response", "id": payload["id"], "success": True, "data": {"cancelled": True}}))
        h.process.stdin.callback = reply
        with self.assertRaisesRegex(RuntimeError, "cancelled by an extension"):
            await h.rpc_command({"type": "new_session"})

    async def test_runtime_restart_cap_terminalizes_without_fake_success(self):
        h = self.host
        s = h._safety_state()
        await h._recover_runtime("agent_idle_timeout", s.epoch)
        await h._recover_runtime("agent_idle_timeout", s.epoch)
        self.assertEqual(h.resume_count, 1)
        self.assertEqual(h.status, "failed")
        self.assertEqual(h.project["delivery_status"], "failed")
        self.assertEqual(s.ledger.restarts, 1)

    async def test_task_budget_does_not_restart(self):
        h = self.host
        s = h._safety_state()
        s.ledger.active_seconds = s.policy.task_seconds
        await h._recover_runtime("task_budget_exhausted", s.epoch)
        self.assertEqual(h.resume_count, 0)
        self.assertEqual(h.status, "failed")

    async def test_user_cancel_wins_over_queued_recovery(self):
        h = self.host
        await h._control_lock.acquire()
        h._request_runtime_recovery("idle")
        h._stopping = True
        h.status = "cancelled"
        h._control_lock.release()
        await h._safety_state().recovery
        self.assertEqual(h.resume_count, 0)
        self.assertEqual(h.status, "cancelled")

    async def test_manual_mode_remains_paused(self):
        h = self.host
        h._safety_state().policy = RuntimePolicy(auto_recover=False)
        await h._recover_runtime("idle", 0)
        self.assertEqual(h.status, "paused")
        self.assertEqual(h.resume_count, 0)

    async def test_recovery_deduplicated(self):
        h = self.host
        h._request_runtime_recovery("one")
        task = h._safety_state().recovery
        h._request_runtime_recovery("two")
        self.assertIs(h._safety_state().recovery, task)
        await task
        self.assertEqual(h.pause_count, 1)

    async def test_cleanup_failure_blocks_restart(self):
        h = self.host
        async def bad_stop():
            raise RuntimeError("old runner still alive")
        h._stop_runtime = bad_stop
        await h._recover_runtime("idle", 0)
        self.assertEqual(h.resume_count, 0)
        self.assertTrue(h._safety_state().cleanup_required)
        self.assertEqual(h.status, "failed")

    async def test_render_timeout_does_not_detach_writer_and_restart(self):
        h = self.host
        h._safety_state().policy = RuntimePolicy(cleanup_seconds=0.01)
        release = asyncio.Event()
        h._host_render_cancel = threading.Event()
        task = asyncio.create_task(release.wait())
        h._transitions.add(task)
        try:
            with self.assertRaisesRegex(RuntimeError, "cleanup did not finish"):
                await h._quiesce_transitions()
            self.assertFalse(task.cancelled())
            self.assertTrue(h._host_render_cancel.is_set())
            self.assertTrue(h._safety_state().cleanup_required)
            self.assertEqual(h.status, "failed")
        finally:
            release.set()
            await task

    async def test_transition_no_longer_blocks_forever(self):
        h = self.host
        s = h._safety_state()
        s.policy = RuntimePolicy(transition_seconds=0.01, poll_seconds=0.002)
        s.transition_started = time.monotonic() - 1
        s.monitor = asyncio.create_task(h._monitor_runtime(0))
        await asyncio.wait_for(s.monitor, 0.3)
        await asyncio.wait_for(s.recovery, 0.3)
        self.assertEqual(s.ledger.faults[-1]["reason"], "host_transition_timeout")

    async def test_stdout_eof_detected(self):
        self.host.process.stdout.feed_eof()
        with self.assertRaisesRegex(RuntimeError, "stdout closed"):
            await self.host._read_stdout()
        self.assertEqual(self.host._safety_state().exit_error, "pi_stdout_closed")

    async def test_old_process_events_are_ignored(self):
        h = self.host
        old = h.process
        reader = asyncio.create_task(h._read_stdout())
        await asyncio.sleep(0)
        h.process = SimpleNamespace(stdin=Writer(), returncode=None)
        old.stdout.feed_data(b'{"type":"agent_start"}\n')
        await asyncio.wait_for(reader, 0.3)
        self.assertEqual(h.core_events, [])

    async def test_partial_rpc_json_is_bounded_by_supervisor(self):
        h = self.host
        s = h._safety_state()
        s.policy = RuntimePolicy(ack_seconds=0.01, poll_seconds=0.002)
        await h.prompt("one")
        h.process.stdout.feed_data(b'{"type":')
        reader = asyncio.create_task(h._read_stdout())
        s.monitor = asyncio.create_task(h._monitor_runtime(0))
        try:
            await asyncio.wait_for(s.monitor, 0.3)
            await asyncio.wait_for(s.recovery, 0.3)
            self.assertEqual(h.resume_count, 1)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def test_usage_counts_message_end_not_streaming_deltas(self):
        h = self.host
        await h.prompt("one")
        for _ in range(10):
            await h._handle_event({"type": "message_update", "message": {"usage": {"input": 50}}})
        await h._handle_event({"type": "message_end", "message": {"role": "assistant", "usage": {"input": 50, "output": 10}}})
        h._save_project(h._project())
        self.assertEqual(h._safety_state().ledger.tokens["input"], 50)
        self.assertEqual(h.project["runtime_metrics"]["assistant_messages"], 1)

    async def test_only_reviewer_json_envelope_normalized(self):
        h = self.host
        body = '```json\n{"status":"fail"}\n```'
        await h._handle_event({"type": "message_end", "message": {"role": "assistant", "text": body}})
        self.assertEqual(h._last_assistant_text, body)
        h.project["workflow"]["mode"] = "scientific_review"
        await h._handle_event({"type": "message_end", "message": {"role": "assistant", "text": body}})
        self.assertEqual(h._last_assistant_text, '{"status":"fail"}')

    async def test_stale_project_save_cannot_erase_runtime_budget(self):
        h = self.host
        old = h._project()
        h._safety_state().ledger.restarts = 2
        h._save_project(old)
        self.assertEqual(h.project["runtime_metrics"]["restarts"], 2)
        self.assertEqual(h.save_calls, 1)

    async def test_publish_does_not_wait_for_disk_and_coalesces_snapshots(self):
        h = self.host
        blocked = threading.Event()
        entered = threading.Event()
        original = h._write_messages
        def write(snapshot):
            entered.set()
            if not blocked.wait(2):
                raise RuntimeError("test release missing")
            original(snapshot)
        h._write_messages = write
        try:
            await h.publish({"id": "same", "content": "first"})
            await asyncio.to_thread(entered.wait, 1)
            await asyncio.wait_for(h.publish({"id": "same", "content": "last"}), 0.1)
            self.assertEqual(h.messages[-1]["content"], "last")
        finally:
            blocked.set()
            if h._safety_state().log_task:
                await h._safety_state().log_task
        self.assertEqual(h.snapshots[-1][-1]["content"], "last")
        self.assertLessEqual(len(h.snapshots), 2)

    async def test_transport_error_recovers_but_scientific_failure_does_not(self):
        h = self.host
        await h._wait_with_errors(["rpc_error: new_session timeout"])
        await h._safety_state().recovery
        self.assertEqual(h.resume_count, 1)
        await h._wait_with_errors(["scientific_acceptance: invalid evidence"])
        self.assertEqual(h.status, "failed")
        self.assertEqual(h.resume_count, 1)

    async def test_incomplete_interrupted_usage_is_not_claimed_complete(self):
        h = self.host
        await h.prompt("one")
        await h._handle_event({"type": "message_end", "message": {"role": "assistant", "usage": {"input": 50, "output": 10}}})
        await h._terminate_process()
        self.assertFalse(h.project["runtime_metrics"]["token_usage_complete"])

    async def test_process_exit_fails_pending_rpc(self):
        h = self.host
        request = asyncio.create_task(h.rpc_command({"type": "get_state"}))
        await asyncio.sleep(0)
        await h._terminate_process()
        with self.assertRaisesRegex(ConnectionError, "stopped"):
            await request
        self.assertFalse(h._pending_rpc)


class PartialDeliveryTests(unittest.TestCase):
    def test_existing_files_are_not_certified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results").mkdir()
            (root / "results" / "numbers.csv").write_text("x\n1\n")
            project = {"workflow": {"current": "p2", "frozen": {"p1": "unchanged"}}}
            result = _write_partial_delivery(root, project, "runtime timeout")
            self.assertEqual(result["delivery_status"], "partial")
            self.assertEqual(result["execution_status"], "failed")
            self.assertEqual(result["scientific_acceptance"], "unchanged")
            self.assertEqual(result["artifacts"][0]["validation"], "not_assessed_by_runtime")
            self.assertEqual(project["workflow"]["frozen"], {"p1": "unchanged"})

    def test_generated_report_alone_does_not_count_as_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for _ in range(2):
                result = _write_partial_delivery(root, {}, "timeout")
                self.assertEqual(result["delivery_status"], "failed")
                self.assertEqual(result["artifacts"], [])

    @unittest.skipIf(os.name == "nt", "Native Windows symlink permission testing is separate")
    def test_symlink_artifacts_are_not_indexed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            (Path(other) / "secret").write_text("private")
            (root / "results").symlink_to(Path(other), target_is_directory=True)
            result = _write_partial_delivery(root, {}, "timeout")
            self.assertFalse(result["artifacts"])

    @unittest.skipIf(os.name == "nt", "Native Windows symlink permission testing is separate")
    def test_symlink_reports_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            (root / "reports").symlink_to(Path(other), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                _write_partial_delivery(root, {}, "timeout")
            self.assertEqual(list(Path(other).iterdir()), [])


if __name__ == "__main__":
    unittest.main()

"""Self-contained runtime safety tests. No model credentials or Pi required."""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi.runtime_support import (
    BudgetLedger, ClientFanout, RuntimeBudgetError, RuntimePolicy, TurnLease,
    atomic_json, bounded_wait, normalize_json_envelope,
)


class PolicyTests(unittest.TestCase):
    def test_default_policy_is_valid(self):
        self.assertGreater(RuntimePolicy().task_seconds, RuntimePolicy().stage_seconds)

    def test_invalid_limits_rejected(self):
        for value in [0, -1, float("inf"), float("nan"), True, "30"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RuntimePolicy(ack_seconds=value)
        with self.assertRaises(ValueError):
            RuntimePolicy(max_task_prompts=2.5)

    def test_zero_restarts_is_allowed(self):
        self.assertEqual(RuntimePolicy(max_stage_restarts=0).max_stage_restarts, 0)

    def test_environment_is_validated(self):
        with patch.dict(os.environ, {"MATHMODEL_RT_AUTO_RECOVER": "false", "MATHMODEL_RT_ACK_SECONDS": "1.5"}):
            policy = RuntimePolicy.from_environment()
            self.assertFalse(policy.auto_recover)
            self.assertEqual(policy.ack_seconds, 1.5)
        with patch.dict(os.environ, {"MATHMODEL_RT_AUTO_RECOVER": "yes"}):
            with self.assertRaises(ValueError):
                RuntimePolicy.from_environment()


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.policy = RuntimePolicy(ack_seconds=3, idle_seconds=5, turn_seconds=30)
        self.lease = TurnLease("one", 0, 30)

    def ack(self):
        self.lease.observe({"type": "response", "id": "one", "success": True}, 1, 20)

    def test_ack_is_not_completion(self):
        self.ack()
        self.assertEqual(self.lease.violation(6, self.policy), "agent_idle_timeout")
        self.assertFalse(self.lease.settled)

    def test_wrong_ack_does_not_clear_watchdog(self):
        self.lease.observe({"type": "response", "id": "other", "success": True}, 1, 20)
        self.assertEqual(self.lease.violation(3, self.policy), "prompt_ack_timeout")

    def test_retries_cannot_hide_idle(self):
        self.ack()
        for second in range(2, 7):
            self.lease.observe({"type": "auto_retry_start"}, second, 20)
        self.assertEqual(self.lease.violation(6, self.policy), "agent_idle_timeout")

    def test_silent_tool_not_idle_but_has_absolute_deadline(self):
        self.ack()
        self.lease.observe({"type": "tool_execution_start", "toolCallId": "t"}, 2, 15)
        self.assertIsNone(self.lease.violation(12, self.policy))
        self.assertEqual(self.lease.violation(17, self.policy), "tool_deadline")

    def test_duplicate_tool_start_cannot_extend_lease(self):
        self.ack()
        for second in (2, 10):
            self.lease.observe({"type": "tool_execution_start", "toolCallId": "t"}, second, 10)
        self.assertEqual(self.lease.tools["t"], 12)

    def test_delta_flood_cannot_extend_turn(self):
        self.ack()
        for second in range(2, 31):
            self.lease.observe({"type": "message_update", "assistantMessageEvent": {"delta": "x"}}, second, 20)
        self.assertEqual(self.lease.violation(30, self.policy), "turn_deadline")

    def test_tool_updates_cannot_extend_tool(self):
        self.ack()
        self.lease.observe({"type": "tool_execution_start", "toolCallId": "t"}, 2, 10)
        self.lease.observe({"type": "tool_execution_update", "toolCallId": "t"}, 11, 100)
        self.assertEqual(self.lease.violation(12, self.policy), "tool_deadline")

    def test_tool_end_returns_to_idle_detection(self):
        self.ack()
        self.lease.observe({"type": "tool_execution_start", "toolCallId": "t"}, 2, 10)
        self.lease.observe({"type": "tool_execution_end", "toolCallId": "t"}, 4, 10)
        self.assertEqual(self.lease.violation(9, self.policy), "agent_idle_timeout")

    def test_settled_lease_does_not_timeout(self):
        self.lease.settled = True
        self.assertIsNone(self.lease.violation(1000, self.policy))


class LedgerTests(unittest.TestCase):
    def test_pause_time_is_not_charged(self):
        ledger = BudgetLedger()
        ledger.tick(0, "a")
        ledger.tick(4, "a", active=False)
        ledger.tick(100, "a")
        ledger.tick(103, "a", active=False)
        self.assertEqual(ledger.active_seconds, 7)

    def test_stage_transition_charges_previous_stage(self):
        ledger = BudgetLedger()
        ledger.tick(0, "a")
        ledger.tick(5, "b")
        ledger.tick(8, "b", active=False)
        self.assertEqual(ledger.stage_seconds, {"a": 5, "b": 3})

    def test_restart_budget_survives_serialization(self):
        policy = RuntimePolicy(max_stage_restarts=1)
        ledger = BudgetLedger()
        self.assertTrue(ledger.reserve_restart("a", policy))
        restored = BudgetLedger.restore(json.loads(json.dumps(ledger.snapshot())))
        self.assertFalse(restored.reserve_restart("a", policy))
        self.assertTrue(restored.reserve_restart("b", policy))
        self.assertEqual(restored.restarts, 2)

    def test_task_restart_budget_caps_different_stages(self):
        policy = RuntimePolicy(max_task_restarts=1)
        ledger = BudgetLedger()
        self.assertTrue(ledger.reserve_restart("a", policy))
        self.assertFalse(ledger.reserve_restart("b", policy))

    def test_prompt_cap_and_task_time_cap(self):
        policy = RuntimePolicy(max_stage_prompts=1, task_seconds=10)
        ledger = BudgetLedger()
        ledger.reserve_prompt("a", policy)
        with self.assertRaisesRegex(RuntimeBudgetError, "stage_prompt_budget"):
            ledger.reserve_prompt("a", policy)
        ledger.active_seconds = 10
        self.assertFalse(ledger.reserve_restart("b", policy))
        with self.assertRaisesRegex(RuntimeBudgetError, "task_budget"):
            ledger.reserve_prompt("b", policy)

    def test_corrupt_metrics_do_not_create_nan_budget(self):
        restored = BudgetLedger.restore({"active_seconds": float("nan"), "prompts": -5, "restarts": True, "tokens": {"input": -2}, "stage_seconds": {"a": "bad"}})
        self.assertEqual(restored.active_seconds, 0)
        self.assertEqual(restored.prompts, 0)
        self.assertEqual(restored.tokens["input"], 0)

    def test_usage_only_counts_valid_final_usage_fields(self):
        ledger = BudgetLedger()
        ledger.record_usage({"usage": {"input": 10, "output": 4, "cacheRead": 2, "cacheWrite": -1}})
        ledger.record_usage({})
        self.assertEqual(ledger.tokens["input"], 10)
        self.assertEqual(ledger.tokens["cacheWrite"], 0)
        self.assertEqual((ledger.assistant_messages, ledger.usage_messages), (2, 0))


class CompleteUsageTests(unittest.TestCase):
    def test_complete_usage_requires_all_fields(self):
        ledger = BudgetLedger()
        ledger.record_usage({"usage": {"input": 1, "output": 2}})
        self.assertEqual(ledger.usage_messages, 0)
        ledger.record_usage({"usage": {"input": 1, "output": 2, "cacheRead": 0, "cacheWrite": 0}})
        self.assertEqual(ledger.usage_messages, 1)


class NormalizationTests(unittest.TestCase):
    def test_only_remove_whole_json_fence(self):
        self.assertEqual(normalize_json_envelope('```json\n{"status":"fail"}\n```'), '{"status":"fail"}')

    def test_invalid_or_ambiguous_content_is_unchanged(self):
        samples = [
            '```json\n{"x":1e999}\n```', '```json\n{"x":1,"x":2}\n```', '```json\n{"x":NaN}\n```',
            'Explanation\n```json\n{"x":1}\n```', '```json\n{"x":1,}\n```',
            '```json\n[1,2]\n```', '```json\n{"status":"FAIL"}\n```\nother',
            '```json\n{"x":1}\n```\n```json\n{"y":2}\n```',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(normalize_json_envelope(sample), sample)

    def test_no_semantic_field_changes(self):
        body = '{"verdict":"INVALID_ENUM","missing_evidence":true,"extra":12}'
        self.assertEqual(normalize_json_envelope('```json\n' + body + '\n```'), body)

    def test_atomic_writer_preserves_old_file_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            atomic_json(path, {"a": 1})
            with self.assertRaises(ValueError):
                atomic_json(path, {"b": float("nan")})
            self.assertEqual(json.loads(path.read_text()), {"a": 1})
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)


class Socket:
    def __init__(self, delay=0):
        self.delay = delay
        self.sent = []
    async def send_json(self, message):
        await asyncio.sleep(self.delay)
        self.sent.append(message)


class FanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_browser_does_not_block_fast_browser(self):
        slow, fast = Socket(10), Socket()
        connected = {slow, fast}
        fan = ClientFanout(RuntimePolicy(websocket_seconds=0.02, cancel_seconds=0.03), connected.discard)
        fan.broadcast(connected, {"id": "1", "content": "ok"})
        await asyncio.sleep(0.06)
        self.assertEqual(fast.sent[0]["content"], "ok")
        self.assertNotIn(slow, connected)
        self.assertEqual(fan.dropped, 1)
        await fan.close()

    async def test_delta_coalescing_is_bounded_and_final_update_survives(self):
        socket = Socket()
        fan = ClientFanout(RuntimePolicy(websocket_pending=2), lambda _: None)
        for i in range(1000):
            fan.offer(socket, {"id": "same", "content": str(i)})
        self.assertEqual(len(fan.clients[socket].pending), 1)
        await asyncio.sleep(0.01)
        self.assertEqual(socket.sent[-1]["content"], "999")
        self.assertEqual(fan.coalesced, 999)
        await fan.close()

    async def test_distinct_message_overflow_disconnects(self):
        socket = Socket(1)
        dropped = []
        fan = ClientFanout(RuntimePolicy(websocket_pending=2), dropped.append)
        for i in range(3):
            fan.offer(socket, {"id": str(i)})
        self.assertNotIn(socket, fan.clients)
        self.assertIn(socket, dropped)
        await fan.close()

    async def test_bounded_wait_does_not_wait_forever_on_cancellation(self):
        release = asyncio.Event()
        async def stubborn():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
        task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        task.cancel()
        pending = await bounded_wait({task}, 0.01)
        self.assertEqual(pending, {task})
        release.set()
        await task


if __name__ == "__main__":
    unittest.main()

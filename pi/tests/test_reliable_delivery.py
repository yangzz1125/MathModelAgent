"""Production-path regressions for bounded delivery; no model calls."""
import asyncio
import py_compile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect
from pi import bridge, compute_jobs
from pi.runtime_support import TurnLease
from pi.tests import test_balanced_workflow as fixtures


class ReliableDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = fixtures.BalancedWorkflowTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.r = self.f.runtime

    async def test_queued_cancel_preserves_terminal_recovery_outcome(self):
        for terminal in ('failed', 'partial', 'completed', 'completed_with_warnings', 'cancelled'):
            with self.subTest(terminal=terminal):
                self.r.status = 'paused'
                async with self.r._control_lock:
                    with patch('pi.bridge._runtime', return_value=self.r):
                        cancellation = asyncio.create_task(bridge.cancel_task(self.r.task_id))
                        await asyncio.sleep(0)
                        self.assertFalse(cancellation.done())
                        self.r.set_status(terminal)
                response = await cancellation
                self.assertFalse(response['success'])
                self.assertEqual(self.r.status, terminal)
                self.assertEqual(self.r._project()['status'], terminal)
                self.r.terminate.assert_not_awaited()

    async def test_exhausted_provider_error_never_advances_or_restarts(self):
        await self.f.plan()
        state = self.r._safety_state()
        state.turn = TurnLease('fixture', 0, 900)
        self.r._stopping = False
        self.r.status = 'running'
        self.r._advance_settled = AsyncMock()
        before = self.r.prompt.await_count
        await self.r._handle_event({'type':'message_end','message':{
            'role':'assistant','content':[], 'stopReason':'error',
            'errorMessage':'OpenAI API error (503): Service temporarily unavailable'}})
        await self.r._handle_event({'type':'agent_settled'})
        await state.recovery
        self.r._advance_settled.assert_not_awaited()
        self.assertEqual(self.r.prompt.await_count, before)
        self.assertEqual(state.ledger.restarts, 0)
        self.assertIn('provider_error:', self.r._project()['runtime_failure'])
        self.assertEqual(self.f.w()['phases'][1]['attempts'], 1)

    async def test_successful_provider_retry_can_advance(self):
        await self.f.plan()
        state = self.r._safety_state()
        state.turn = TurnLease('fixture', 0, 900)
        self.r._settled = AsyncMock()
        await self.r._handle_event({'type':'message_end','message':{
            'role':'assistant','content':[], 'stopReason':'error','errorMessage':'terminated'}})
        await self.r._handle_event({'type':'auto_retry_start'})
        await self.r._handle_event({'type':'message_end','message':{
            'role':'assistant','content':[{'type':'text','text':'done'}], 'stopReason':'stop'}})
        await self.r._handle_event({'type':'agent_settled'})
        await asyncio.gather(*self.r._transitions)
        self.r._settled.assert_awaited_once()
        self.assertIsNone(state.recovery)

    async def test_scientific_rejection_survives_provider_failure(self):
        await self.f.plan()
        await self.f.solve()
        self.r._last_assistant_text = fixtures.review(verdict='revise')
        await self.r._advance_settled()
        await self.r._finalize_runtime_failure('provider_error: HTTP 503')
        self.assertEqual(self.f.w()['last_review']['verdict'], 'revise')
        self.assertEqual(self.f.w()['last_review']['issues'], ['method is invalid'])
        self.assertFalse(self.f.w()['outcomes'])

    async def test_closed_transport_recovers_through_real_resume(self):
        self.r.status = 'failed'
        self.r.run = AsyncMock()
        await self.r._recover_runtime('bridge_error: Pi stdout closed', 0, closed=True)
        self.assertEqual(self.r.status, 'starting')
        self.assertFalse(self.r._safety_state().cleanup_required)
        self.assertEqual(self.r._project()['resume_count'], 1)
        await self.r.runner
        self.r.run.assert_awaited_once()

    async def test_closed_transport_manual_mode_stays_paused(self):
        self.r.status = 'failed'
        state = self.r._safety_state()
        state.policy = replace(state.policy, auto_recover=False)
        await self.r._recover_runtime('bridge_error: Pi stdout closed', 0, closed=True)
        self.assertEqual(self.r.status, 'paused')
        self.assertFalse(state.cleanup_required)
        self.assertIsNone(self.r.runner)

    async def test_resume_error_does_not_invent_cleanup_failure(self):
        self.r.status = 'failed'
        self.r._resume = AsyncMock(side_effect=ValueError('artifact_changed: invalid checkpoint'))
        await self.r._recover_runtime('bridge_error: Pi stdout closed', 0, closed=True)
        self.assertEqual(self.r.status, 'failed')
        self.assertFalse(self.r._safety_state().cleanup_required)

    async def test_cleanup_failure_blocks_repair_and_persists_ownership(self):
        await self.f.plan()
        fixtures.solver(self.f.root)
        original = compute_jobs._stop_tree
        attempts = []
        async def failing_cleanup(process, job=None):
            attempts.append((process, job))
            raise OSError('simulated native cleanup error')
        before = self.r.prompt.await_count
        try:
            with patch('pi.compute_jobs._stop_tree', side_effect=failing_cleanup):
                await self.r._advance_settled()
            self.assertTrue(self.r._safety_state().cleanup_required)
            self.assertEqual(self.r.status, 'failed')
            self.assertEqual(self.r.prompt.await_count, before)
            self.r.terminate.assert_awaited_once()
            held = self.r._safety_state().host_cleanup_error
            self.assertIs(held.process, attempts[0][0])
            fresh = bridge.TaskRuntime('f'*12, self.f.root, status='paused')
            with self.assertRaises(HTTPException):
                await fresh._resume()
            with self.assertRaisesRegex(RuntimeError, 'cleanup'):
                await self.r._quiesce_transitions()
        finally:
            for process, job in attempts:
                await original(process, job)

    async def test_bytecode_change_reruns_instead_of_accepting_stale_receipt(self):
        await self.f.plan()
        fixtures.solver(self.f.root)
        target = self.f.root/'code/q1/helper.pyc'
        script = self.f.root/'code/q1/solve.py'
        script.write_text('import helper\nassert helper.VALUE == 12\n'+script.read_text())
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'helper.py'
            source.write_text('VALUE = 12\n')
            py_compile.compile(str(source), cfile=str(target), doraise=True)
            await self.r._advance_settled()
            self.assertIn('code/q1/helper.pyc', self.f.w()['job_receipts']['q1']['source_hashes'])
            source.write_text('VALUE = 99\n')
            py_compile.compile(str(source), cfile=str(target), doraise=True)
            p = self.r._project()
            p['workflow']['mode'] = 'solve'
            self.r._save_project(p)
            await self.r._advance_settled()
            self.assertNotEqual(self.f.w()['mode'], 'scientific_review')
            self.assertEqual(self.f.w().get('cache_hits', 0), 0)

    async def test_legacy_bytecode_snapshot_cannot_silently_reuse_receipt(self):
        await self.f.plan()
        await self.f.solve()
        helper = self.f.root/'code/q1/helper.pyc'
        helper.write_bytes(b'previously untracked dependency')
        p = self.r._project()
        p['workflow']['mode'] = 'compute'
        self.r._save_project(p)
        before = self.r.prompt.await_count
        with patch('pi.efficient_workflow.run_job', side_effect=AssertionError('unsafe recompute')):
            await self.r._v4_host_step()
        self.assertEqual(self.r.status, 'failed')
        self.assertEqual(self.r.prompt.await_count, before)

    async def test_cancel_cleanup_failure_is_not_lost_when_transition_finishes(self):
        await self.f.plan()
        fixtures.solver(self.f.root)
        script = self.f.root/'code/q1/solve.py'
        script.write_text('import time\ntime.sleep(60)\n')
        original = compute_jobs._stop_tree
        attempts = []
        spawned = asyncio.Event()
        spawn = asyncio.create_subprocess_exec
        async def capture_spawn(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            spawned.set()
            return process
        async def failing_cleanup(process, job=None):
            attempts.append((process, job))
            raise OSError('cleanup during cancellation failed')
        try:
            with patch('pi.compute_jobs.asyncio.create_subprocess_exec', side_effect=capture_spawn), \
                 patch('pi.compute_jobs._stop_tree', side_effect=failing_cleanup):
                task = asyncio.create_task(self.r._advance_settled())
                self.r._transitions.add(task)
                await asyncio.wait_for(spawned.wait(), 5)
                self.r._stopping = True
                with self.assertRaisesRegex(RuntimeError, 'cleanup'):
                    await self.r._quiesce_transitions()
                await task
                self.r._transitions.discard(task)
                with self.assertRaisesRegex(RuntimeError, 'cleanup'):
                    await self.r._quiesce_transitions()
                self.assertTrue(self.r._project()['runtime_metrics']['cleanup_required'])
        finally:
            for process, job in attempts:
                await original(process, job)

    async def test_bytecode_added_to_frozen_tree_is_fatal(self):
        await self.f.plan()
        await self.f.solve()
        await self.f.accept()
        cache = self.f.root/'code/q1/__pycache__/helper.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'new executable dependency')
        await self.r._advance_settled()
        self.assertEqual(self.r.status, 'failed')


class FreeformRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_autonomous_versions_reject_websocket_prompt(self):
        for version in (3, 4):
            for mode in ('compute', 'scientific_review', 'write'):
                with self.subTest(version=version, mode=mode):
                    f = fixtures.BalancedWorkflowTests()
                    f.setUp()
                    try:
                        r = f.runtime
                        r._project = lambda: {'workflow': {'contract_version': version, 'mode': mode}}
                        socket = AsyncMock()
                        socket.receive_json.side_effect = [
                            {'type': 'prompt', 'message': 'interrupt Host'},
                            WebSocketDisconnect(),
                        ]
                        with patch('pi.bridge._runtime', return_value=r):
                            await bridge.task_socket(socket, r.task_id)
                        r.prompt.assert_not_awaited()
                        socket.accept.assert_awaited_once()
                        await r._safety_fanout().close()
                    finally:
                        f.doCleanups()


if __name__ == '__main__':
    unittest.main()

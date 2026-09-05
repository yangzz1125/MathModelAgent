"""Regression checks for the September workflow audit; isolated tasks, no model calls."""
import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, UploadFile
from pi import bridge
from pi.scientific_review import ScientificContractError, candidate_errors, validate_paper_manifest, paper_source_errors
from pi.staged_workflow import method_version_dir, validate_method_card, validate_problem_inventory, workspace_hashes
from pi.tests import test_bridge as fixtures


class WorkflowRegressionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.IncrementalPlanningV3Test()
        self.key = patch.object(bridge, '_host_transition_key', return_value=b'isolated-regression-key')
        self.key.start()
        self.addCleanup(self.key.stop)

    async def test_signed_review_replay_rejects_changed_candidate_before_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            runtime._last_assistant_text = self.fixture._method_review()
            await runtime._settled()
            fixtures.ScientificRuntimeTest()._candidate(runtime)
            project = runtime._project()
            project['workflow']['mode'] = 'scientific_review'
            project['workflow']['review_snapshot'] = workspace_hashes(runtime.workspace)
            runtime._authorize_transition(project, 'scientific_review', json.loads(fixtures.ScientificRuntimeTest()._review()))
            (runtime.workspace / 'results/q1/evidence.json').write_text('{"changed": true}')
            runtime._complete_current = AsyncMock()
            await runtime._settled()
            self.assertEqual(runtime.status, 'failed')
            self.assertIn('reviewed artifacts changed', runtime._current_phase(runtime._project()['workflow'])['last_error'])
            runtime._complete_current.assert_not_awaited()
            self.assertEqual(runtime._ledger()['problems']['q1']['status'], 'provisional')

    async def test_pending_method_revision_restores_version_before_card_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            review = json.loads(self.fixture._method_review())
            review.update(verdict='revise', method_validity='fail', issue_class='method', issues=['wrong method'], required_repairs=['revise'])
            runtime._authorize_transition(runtime._project(), 'method_audit', review)
            ledger = runtime._ledger()
            ledger['problems']['q1']['proposal_version'] = 2
            runtime._save_ledger(ledger)
            await runtime._settled()
            self.assertEqual(runtime.status, 'running')
            self.assertEqual(runtime._project()['workflow']['current'], 'method:q1')
            self.assertEqual(runtime._ledger()['problems']['q1']['proposal_version'], 2)

    async def test_supplemental_lookup_never_uses_reused_primary_version(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            workflow = runtime._project()['workflow']
            card = runtime._method_card(workflow)
            card['proposal_version'] = 2
            workflow.update(supplemental_spike=True, supplemental_spike_ids=['q1.spike.question'])
            with patch.object(bridge, 'validate_spike_report', return_value={}) as validate:
                runtime._spike_report(workflow, card)
                self.assertTrue(validate.call_args.kwargs['supplemental'])
                self.assertEqual(validate.call_args.kwargs['source_version'], 2)
                runtime._spike_report(workflow, card, supplemental=False)
                self.assertFalse(validate.call_args.kwargs['supplemental'])
                self.assertEqual(validate.call_args.kwargs['source_version'], 1)

    async def test_revision_guards_reject_renamed_claim_and_changed_downgrade_spec(self):
        for downgrade in (False, True):
            with self.subTest(downgrade=downgrade), tempfile.TemporaryDirectory() as directory:
                runtime = self.fixture._runtime_at_method_audit(directory, evidence_level='A_certified')
                project = runtime._project()
                base = runtime._method_card(project['workflow'])
                inventory = validate_problem_inventory(runtime.workspace, 1)
                raw = self.fixture._card(inventory)
                raw['proposal_version'] = 2
                workflow = project['workflow']
                workflow.update(current='method:q1', mode='evidence_downgrade' if downgrade else 'method_revision')
                if downgrade:
                    workflow.update(downgrade_base_problem=base['problem'], downgrade_base_card=base, downgrade_claim_ids=['q1.objective'])
                    raw['finite_domain'] = 'Different domain'
                else:
                    workflow['revision_base_evidence_levels'] = {'q1.objective': 'A_certified'}
                    raw['problem']['claims'][0]['id'] = 'q1.renamed'
                    raw['problem']['independent_validation'][0]['claims'] = ['q1.renamed']
                self.fixture._write(method_version_dir(runtime.workspace, 'q1', 2) / 'method_card.json', raw)
                (runtime.workspace / 'reports/q1_METHOD_v2.md').write_text('revised')
                card = validate_method_card(runtime.workspace, inventory, 'q1', 2)
                runtime._save_project(project)
                runtime._complete_current = AsyncMock()
                await runtime._finish_method_artifact_v3(project, card)
                self.assertEqual(runtime.status, 'failed')
                runtime._complete_current.assert_not_awaited()

    async def test_scientific_blocked_and_exhausted_revisions_never_restart(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked), tempfile.TemporaryDirectory() as directory:
                runtime = self.fixture._runtime_at_method_audit(directory)
                runtime._last_assistant_text = self.fixture._method_review()
                await runtime._settled()
                project = runtime._project()
                workflow = project['workflow']
                workflow.update(current='problem:q1', mode='scientific_review')
                phase = runtime._current_phase(workflow)
                phase['replan_attempts'] = 0 if blocked else 1
                runtime._save_project(project)
                runtime._restart_v3_problem_planning = AsyncMock()
                review = json.loads(fixtures.ScientificRuntimeTest()._review('blocked' if blocked else 'reject', 'blocked' if blocked else 'method'))
                await runtime._start_v3_method_revision_from_science(project, review)
                self.assertEqual(runtime.status, 'failed')
                runtime._restart_v3_problem_planning.assert_not_awaited()
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            project = runtime._project()
            runtime._current_phase(project['workflow'])['attempts'] = 3
            runtime._save_project(project)
            await runtime._restart_v3_problem_planning(project, {'issues': ['rejected']})
            self.assertEqual(runtime.status, 'failed')
            self.assertEqual(runtime._ledger()['problems']['q1']['proposal_version'], 1)

    async def test_candidate_empty_objects_do_not_pass_schema_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            problem = runtime._method_card(runtime._project()['workflow'])['problem']
            for name in ('result.json', 'verification.json'):
                self.fixture._write(runtime.workspace / 'results/q1' / name, {})
            errors = candidate_errors(runtime.workspace, problem)
            self.assertIn('candidate_protocol: result problem_id mismatch', errors)
            self.assertIn('candidate_protocol: verification schema/status', errors)

    async def test_upload_aliases_and_notes_collisions_are_rejected_without_overwrite(self):
        def upload(name):
            return UploadFile(filename=name, file=io.BytesIO(b'original'))
        cases = [('', [upload('A.csv'), upload('a.csv')]), ('notes', [upload('user_notes.md')])]
        for question, uploads in cases:
            with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'WORKSPACES', Path(directory)), patch.object(bridge, 'TASKS', {}):
                with self.assertRaises(HTTPException) as error:
                    await bridge._initialize_project(question=question, source_folder='', files=uploads, relative_paths=[])
                self.assertEqual(error.exception.status_code, 400)
                self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_user_requirements_persist_across_resume_and_paper_context(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'WORKSPACES', Path(directory)), patch.object(bridge, 'TASKS', {}):
            runtime, _ = await bridge._initialize_project(question='', source_folder='', files=[UploadFile(filename='problem.md', file=io.BytesIO(b'problem'))], relative_paths=[])
            with patch.object(bridge, 'figure_stack_errors', return_value=[]), patch.object(bridge, '_document_stack_errors', return_value=[]), patch.object(bridge, '_task_model_config', return_value=('test', 'high')), patch.object(runtime, 'run', new_callable=AsyncMock):
                await bridge._start_project(runtime, bridge.StartProjectRequest(question='REQUIRED_EXACT_RESULTS'))
                await runtime.runner
            project = runtime._project()
            self.assertIn('REQUIRED_EXACT_RESULTS', runtime._resume_prompt(project))
            notes = project['user_requirements_file']
            self.assertIn(notes, runtime._stage_context_paths(project))
            self.assertIn(notes, runtime._paper_context_paths(project, writing=True))
            self.assertEqual((runtime.workspace / notes).read_text().strip(), 'REQUIRED_EXACT_RESULTS')

    async def test_cancel_survives_broken_rpc_pipe(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            runtime.process = SimpleNamespace(returncode=None, stdin=True)
            runtime.send_rpc = AsyncMock(side_effect=BrokenPipeError('closed'))
            runtime.terminate = AsyncMock()
            await runtime.abort()
            self.assertEqual(runtime.status, 'cancelled')
            runtime.terminate.assert_awaited_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree cleanup')
    async def test_job_teardown_is_single_flight(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            runtime.terminate = bridge.TaskRuntime.terminate.__get__(runtime)
            boundary = SimpleNamespace(job_handle=1, job_assigned=True, terminate_job=Mock())
            boundary.terminate_job.side_effect = lambda: setattr(boundary, 'job_handle', None)
            runtime._host_boundary = boundary
            entered, release = asyncio.Event(), asyncio.Event()
            async def wait():
                entered.set()
                await release.wait()
                runtime.process.returncode = 0
                return 0
            runtime.process = SimpleNamespace(pid=424242, returncode=None, wait=wait)
            runtime._release_host_state = Mock()
            first = asyncio.create_task(runtime.terminate())
            await entered.wait()
            with patch.object(bridge.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as spawn:
                second = asyncio.create_task(runtime.terminate())
                await asyncio.sleep(0)
                release.set()
                await asyncio.gather(first, second)
                spawn.assert_not_awaited()

    async def test_cancel_during_startup_cannot_send_late_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            runtime._acquire_host_state = Mock()
            runtime._read_stdout = AsyncMock()
            runtime._read_stderr = AsyncMock()
            runtime.send_rpc = AsyncMock()
            runtime.terminate = AsyncMock()
            entered, release = asyncio.Event(), asyncio.Event()
            async def spawn(*args, **kwargs):
                entered.set()
                await release.wait()
                return SimpleNamespace(pid=123456, returncode=None)
            with patch.object(bridge.asyncio, 'create_subprocess_exec', side_effect=spawn), patch.object(bridge.shutil, 'which', return_value='mock-pi'):
                runtime.runner = asyncio.create_task(runtime.run('prompt'))
                runner = runtime.runner
                await entered.wait()
                cancellation = asyncio.create_task(runtime.abort())
                await asyncio.sleep(0)
                self.assertEqual(runtime.status, 'cancelled')
                release.set()
                await asyncio.gather(runner, cancellation)
            self.assertEqual(runtime.status, 'cancelled')
            runtime.prompt.assert_not_awaited()

    async def test_pause_cancels_awaiting_transition_before_saving_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            runtime._last_assistant_text = self.fixture._method_review()
            entered = asyncio.Event()
            async def switch(*args):
                entered.set()
                await asyncio.Event().wait()
            runtime._switch_session = switch
            transition = asyncio.create_task(runtime._settled())
            await entered.wait()
            await runtime.pause()
            self.assertTrue(transition.cancelled())
            self.assertEqual(runtime.status, 'paused')
            self.assertEqual(runtime._project()['status'], 'paused')
            runtime.prompt.assert_not_awaited()

    async def test_prompt_rejection_is_paused_and_ack_is_correlated(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.fixture._runtime_at_method_audit(directory)
            runtime.prompt = bridge.TaskRuntime.prompt.__get__(runtime)
            runtime.send_rpc = AsyncMock()
            runtime.pause = AsyncMock()
            await runtime.prompt('request', initial=True)
            request_id = runtime.send_rpc.call_args.args[0]['id']
            await runtime._handle_event({'type': 'response', 'command': 'prompt', 'id': 'stale', 'success': True})
            self.assertIn(request_id, runtime._prompt_watchdogs)
            await runtime._handle_event({'type': 'response', 'command': 'prompt', 'id': request_id, 'success': False, 'error': 'missing credentials'})
            await asyncio.sleep(0)
            runtime.pause.assert_awaited_once_with('rpc_error: missing credentials')
            self.assertFalse(runtime._prompt_watchdogs)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object lifecycle')
    async def test_real_process_pause_resume_without_model_calls(self):
        fake_rpc = "import sys,json\nfor line in sys.stdin:\n c=json.loads(line)\n print(json.dumps({'type':'response','id':c.get('id'),'command':c['type'],'success':True,'data':{}}),flush=True)\n"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'LOCALAPPDATA': directory}):
            runtime = self.fixture._runtime_at_method_audit(str(Path(directory) / 'task'))
            runtime.prompt = bridge.TaskRuntime.prompt.__get__(runtime)
            runtime.terminate = bridge.TaskRuntime.terminate.__get__(runtime)
            acknowledged = asyncio.Event()
            handle = runtime._handle_event
            async def observed(event):
                await handle(event)
                if event.get('command') == 'prompt':
                    acknowledged.set()
            runtime._handle_event = observed
            spawn = asyncio.create_subprocess_exec
            async def fake_pi(*args, **kwargs):
                if '--mode' in args:
                    return await spawn(sys.executable, '-u', '-c', fake_rpc, **kwargs)
                return await spawn(*args, **kwargs)
            with patch.object(bridge.asyncio, 'create_subprocess_exec', side_effect=fake_pi):
                try:
                    runtime.runner = asyncio.create_task(runtime.run('fake prompt'))
                    await asyncio.wait_for(acknowledged.wait(), timeout=10)
                    first = runtime.process
                    await runtime.pause()
                    self.assertIsNotNone(first.returncode)
                    self.assertEqual(runtime.status, 'paused')
                    self.assertIsNone(runtime._host_boundary)
                    acknowledged.clear()
                    await runtime.resume()
                    await asyncio.wait_for(acknowledged.wait(), timeout=10)
                    second = runtime.process
                    self.assertNotEqual(first.pid, second.pid)
                    await runtime.pause()
                    self.assertIsNotNone(second.returncode)
                    self.assertEqual(runtime._project()['pause_count'], 2)
                    self.assertEqual(runtime._project()['resume_count'], 1)
                    self.assertFalse(runtime._prompt_watchdogs)
                finally:
                    await runtime.terminate()
                    if runtime.runner and not runtime.runner.done():
                        await asyncio.wait_for(runtime.runner, timeout=10)

    async def test_manifest_requires_reachable_uncommented_anchors_and_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / 'paper'
            paper.mkdir()
            master = paper / 'main.tex'
            master.write_text('No claims here')
            anchors = {name: name + '_marker' for name in ('model', 'algorithm', 'result', 'validation', 'conclusion', 'limitation')}
            chapter = paper / 'chapter.tex'
            chapter.write_text('\n'.join(anchors.values()))
            plan = {'plan_version': 1, 'coverage': [{'claim_id': 'q1.claim', 'problem_id': 'q1', 'figures': []}]}
            manifest = {'schema_version': 1, 'plan_version': 1, 'coverage': [{'claim_id': 'q1.claim', 'section_file': 'paper/chapter.tex', 'anchors': anchors, 'figures': []}]}
            (paper / 'paper_manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ScientificContractError, 'not reachable'):
                validate_paper_manifest(root, plan, strict=True)
            master.write_text('\\input{chapter}')
            self.assertEqual(validate_paper_manifest(root, plan, strict=True), manifest)
            chapter.write_text('% ' + ' '.join(anchors.values()))
            with self.assertRaisesRegex(ScientificContractError, 'uncommented'):
                validate_paper_manifest(root, plan, strict=True)
            master.write_text('\\cite{missing}')
            self.assertEqual(paper_source_errors(root, strict=True), ["paper_references: citations without bibitems: ['missing']"])


if __name__ == '__main__':
    unittest.main()

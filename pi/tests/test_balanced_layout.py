"""Balanced workflow must retain the versioned upstream layout contract."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from pi import bridge
from pi.paper_layout import LAYOUT_SOURCE, LAYOUT_VERSION
from pi.tests import test_balanced_workflow as fixtures

class BalancedLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_request_pins_layout_from_selected_options(self):
        with tempfile.TemporaryDirectory() as d, patch.object(bridge, 'WORKSPACES', Path(d)):
            runtime, _ = await bridge._initialize_project(question='A modeling question.', source_folder='', files=[], relative_paths=[])
            runtime.run = AsyncMock()
            try:
                with patch('pi.bridge.figure_stack_errors', return_value=[]), patch('pi.bridge._document_stack_errors', return_value=[]):
                    await bridge._start_project(runtime, bridge.StartProjectRequest(competition='CUMCM', language='Chinese', paper_engine='LaTeX', planner_model='openai/gpt-5.6-sol', worker_model='openai/gpt-5.6-luna'))
                await runtime.runner
                self.assertEqual(runtime._project()['workflow']['paper_layout'], LAYOUT_VERSION)
            finally:
                bridge.TASKS.pop(runtime.task_id, None)

    async def test_host_copies_layout_and_write_gate_rejects_wrong_master(self):
        fixture=fixtures.BalancedWorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        runtime=fixture.runtime
        project=runtime._project();project['workflow']['paper_layout']=LAYOUT_VERSION
        runtime._save_project(project)
        await fixture.plan();await fixture.solve();await fixture.accept()
        self.assertEqual((fixture.root/'paper/cumcm-layout.tex').read_bytes(),LAYOUT_SOURCE.read_bytes())
        self.assertIn('Paper layout contract',runtime.prompt.call_args.args[0])
        fixtures.write(fixture.root,'paper/coverage.json',{'covered_problem_ids':['q1'],'missing_problem_ids':[]})
        fixtures.write(fixture.root,'paper/main.tex',r'\documentclass{article}\begin{document}Wrong layout.\end{document}')
        await runtime._advance_settled()
        self.assertEqual(fixture.w()['mode'],'write')
        self.assertIn('paper_layout',runtime.prompt.call_args.args[0])
        self.assertNotIn('paper_build',fixture.w())

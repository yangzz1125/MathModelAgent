"""Balanced v4 engineering regressions, not a live-model accuracy benchmark."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from pi import bridge
from pi.compute_jobs import hashes,run_job,safe_path
from pi.efficient_contract import object_json,plan_contract,result_contract
from pi.efficient_workflow import initial_balanced_workflow
from pi.runtime_support import RuntimePolicy


def item(pid='q1', depends=None):
    return dict(id=pid,question='Maximize 3x+2y, integer x,y>=0, x+y<=4',
                method='enumeration',fallback='simple feasible greedy',depends_on=depends or [],
                acceptance=['feasible integer witness','objective=12'],runtime_seconds=10)


def write(root,relative,value):
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value if isinstance(value,str) else json.dumps(value),encoding='utf-8')


def solver(root,pid='q1',fail=False):
    code=f'''import json,pathlib
root=pathlib.Path('results/{pid}');root.mkdir(parents=True,exist_ok=True)
x,y=max(((x,y) for x in range(5) for y in range(5) if x+y<=4),key=lambda p:3*p[0]+2*p[1])
(root/'witness.json').write_text(json.dumps({{'x':x,'y':y}}))
(root/'result.json').write_text(json.dumps({{'problem_id':'{pid}','status':'candidate','metrics':[{{'name':'objective','value':3*x+2*y}}],'assumptions':[],'limitations':[],'figures':[]}}))
'''
    write(root,f'code/{pid}/solve.py','raise RuntimeError("simulated solver failure")\n' if fail else code)
    write(root,f'code/{pid}/validate.py',f'''import json,pathlib
root=pathlib.Path('results/{pid}')
w=json.loads((root/'witness.json').read_text());r=json.loads((root/'result.json').read_text())
x,y=w['x'],w['y']
ok=isinstance(x,int) and isinstance(y,int) and x>=0 and y>=0 and x+y<=4 and r['metrics'][0]['value']==3*x+2*y==12
(root/'validation.json').write_text(json.dumps({{'independent_method':'analytic bound 3x+2y<=3(x+y)<=12 and feasible witness','checks':[{{'name':'upper bound and witness','passed':ok}}]}}))
''')


def review(pid='q1',verdict='accept'):
    return json.dumps(dict(verdict=verdict,issues=[] if verdict=='accept' else ['method is invalid'],
                           warnings=[],reason='checked independent bound and feasible witness',
                           evidence_paths=[f'results/{pid}/validation.json']))


class ContractTests(unittest.TestCase):
    def test_json_envelope_without_semantic_guessing(self):
        self.assertEqual(object_json('```json\n{"n":2}\n```'),{'n':2})
        for bad in ('{"x":1,"x":2}','{"x":NaN}','{"x":1e9999}','[]','hello {"x":1}'):
            with self.subTest(bad=bad),self.assertRaises(ValueError): object_json(bad)

    def test_topology_and_finite_runtime(self):
        self.assertEqual([p['id'] for p in plan_contract({'problems':[item('q2',['q1']),item()]})],['q1','q2'])
        bad_values=([item('q1',['q1'])],[item('q1',['q9'])],[item(),item()],[],[dict(item(),runtime_seconds=float('nan'))])
        for bad in bad_values:
            with self.subTest(bad=bad),self.assertRaises(ValueError): plan_contract({'problems':bad})

    def test_path_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('../x','/tmp/x','C:/x','code\\x.py'):
                with self.subTest(name=name),self.assertRaises(ValueError): safe_path(Path(d),name)


class HostJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_tail_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            r=await run_job([sys.executable,'-c',"print('x'*30000);print('last')"],Path(d),seconds=5,max_output=100)
            self.assertEqual(r.returncode,0);self.assertTrue(r.output_truncated)
            self.assertLessEqual(len(r.output),100);self.assertTrue(r.output.rstrip().endswith('last'))

    async def test_silent_hang_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            start=time.monotonic();r=await run_job([sys.executable,'-c','import time;time.sleep(60)'],Path(d),seconds=.2)
            self.assertTrue(r.timed_out);self.assertLess(time.monotonic()-start,8)

    async def test_cancellation_stops_child(self):
        with tempfile.TemporaryDirectory() as d:
            task=asyncio.create_task(run_job([sys.executable,'-c','import time;time.sleep(60)'],Path(d),seconds=60))
            await asyncio.sleep(.2);task.cancel()
            with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(task,8)

    async def test_exited_parent_cannot_leave_open_pipe_child(self):
        with tempfile.TemporaryDirectory() as d:
            r=await run_job([sys.executable,'-c','import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])'],Path(d),seconds=.3)
            self.assertTrue(r.timed_out)


class BalancedWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        write(self.root,'input/problem.md','Maximize 3x+2y, integer x,y>=0, x+y<=4.')
        write(self.root,'input_manifest.json',{'problem_file':'input/problem.md'})
        write(self.root,'planning/ledger.json',{'schema_version':1})
        write(self.root,'project.json',{'status':'ready'})
        self.runtime=bridge.TaskRuntime('f'*12,self.root,status='running')
        self.runtime._safety_state()
        for name in ('prompt','system','terminate','_switch_session'): setattr(self.runtime,name,AsyncMock())
        profiles={'planner':{'model':'test/p','thinking':'low'},'worker':{'model':'test/w','thinking':'low'}}
        self.runtime._save_project(dict(status='running',problem_file='input/problem.md',language='English',paper_engine='LaTeX',workflow=initial_balanced_workflow(profiles,self.root)))

    def w(self): return self.runtime._project()['workflow']
    async def plan(self,problems=None):
        write(self.root,'planning/plan.json',{'problems':problems or [item()]})
        await self.runtime._advance_settled();self.assertEqual(self.w()['mode'],'solve')
    async def solve(self,pid='q1'):
        solver(self.root,pid);await self.runtime._advance_settled()
        self.assertEqual(self.w()['mode'],'scientific_review',self.w())
    async def accept(self,pid='q1'):
        self.runtime._last_assistant_text=review(pid);await self.runtime._advance_settled()

    async def test_real_calculation_independent_review_freezes_helpers(self):
        await self.plan();await self.solve()
        self.assertEqual(result_contract(self.root,'q1')['metrics'][0]['value'],12)
        self.assertFalse(self.w()['outcomes']);await self.accept()
        self.assertEqual(self.w()['mode'],'write')
        self.assertEqual(self.w()['outcomes']['q1']['status'],'accepted')
        self.assertIn('code/q1/validate.py',self.w()['protected'])
        self.assertEqual(len(self.w()['job_receipts']['q1']['jobs']),2)

    async def test_forged_state_is_rejected(self):
        data=json.loads((self.root/'project.json').read_text());data['workflow']['outcomes']['q1']={'status':'accepted'}
        write(self.root,'project.json',data)
        with self.assertRaisesRegex(ValueError,'signature'): self.runtime._project()

    async def test_input_change_is_fatal(self):
        await self.plan();write(self.root,'input/problem.md','tampered');await self.runtime._advance_settled()
        self.assertEqual(self.runtime.status,'failed')

    async def test_new_file_in_frozen_tree_is_detected(self):
        await self.plan();await self.solve();await self.accept()
        write(self.root,'code/q1/extra.py','print("tampered")');await self.runtime._advance_settled()
        self.assertEqual(self.runtime.status,'failed')

    async def test_failed_dependency_does_not_stop_independent_question(self):
        await self.plan([item(),item('q2',['q1']),item('q3')])
        for _ in range(3):
            solver(self.root,fail=True);await self.runtime._advance_settled()
        self.assertEqual(self.w()['outcomes']['q1']['status'],'unresolved')
        self.assertEqual(self.w()['current'],'problem:q3')
        await self.solve('q3');await self.accept('q3');self.assertEqual(self.w()['mode'],'write')
        self.assertEqual(self.w()['outcomes']['q2']['status'],'blocked')

    async def test_review_protocol_exhaustion_never_accepts(self):
        await self.plan([item(),item('q2')]);await self.solve();self.runtime._last_assistant_text='not JSON'
        await self.runtime._advance_settled();await self.runtime._advance_settled()
        self.assertEqual(self.w()['outcomes']['q1']['status'],'unresolved')
        self.assertEqual(self.w()['current'],'problem:q2')

    async def test_reviewer_cannot_mutate_candidate(self):
        await self.plan();await self.solve();write(self.root,'results/q1/result.json',{})
        self.runtime._last_assistant_text=review();await self.runtime._advance_settled()
        self.assertEqual(self.runtime.status,'failed')

    async def test_rejected_review_switches_to_writable_repair_session(self):
        await self.plan();await self.solve();self.runtime._last_assistant_text=review(verdict='revise')
        await self.runtime._advance_settled();self.assertEqual(self.w()['mode'],'solve')
        self.runtime._switch_session.assert_awaited_with('worker')

    async def test_compute_cache_avoids_duplicate_jobs(self):
        await self.plan();await self.solve();p=self.runtime._project();p['workflow']['mode']='compute';self.runtime._save_project(p)
        with patch('pi.efficient_workflow.run_job',side_effect=AssertionError('cache reran computation')):
            await self.runtime._v4_host_step()
        self.assertEqual(self.w()['cache_hits'],1)

    async def test_helper_change_invalidates_cache(self):
        await self.plan();await self.solve();write(self.root,'code/q1/helper.py','x=1')
        p=self.runtime._project();p['workflow']['mode']='solve';self.runtime._save_project(p)
        await self.runtime._advance_settled();self.assertEqual(self.w()['compute_jobs'],4)

    async def test_noop_cannot_reuse_stale_results(self):
        await self.plan();await self.solve()
        write(self.root,'code/q1/solve.py','pass');write(self.root,'code/q1/validate.py','pass')
        p=self.runtime._project();p['workflow']['mode']='solve';self.runtime._save_project(p)
        await self.runtime._advance_settled();self.assertEqual(self.w()['mode'],'solve')
        self.assertFalse((self.root/'results/q1/result.json').exists())

    async def test_false_check_and_nonfinite_metric_are_not_candidates(self):
        await self.plan();await self.solve()
        r=json.loads((self.root/'results/q1/result.json').read_text())
        for value in (True,float('nan'),float('inf')):
            r['metrics'][0]['value']=value;write(self.root,'results/q1/result.json',r)
            with self.subTest(value=value),self.assertRaises(ValueError): result_contract(self.root,'q1')

    async def test_missing_compiler_keeps_results_without_more_model_calls(self):
        await self.plan();await self.solve();await self.accept()
        p=self.runtime._project();p['workflow']['mode']='compile';self.runtime._save_project(p)
        count=self.runtime.prompt.await_count
        with patch('pi.efficient_workflow.shutil.which',return_value=None): await self.runtime._v4_host_step()
        self.assertEqual(self.runtime.status,'partial');self.assertEqual(count,self.runtime.prompt.await_count)

    @unittest.skipUnless(shutil.which('xelatex') and shutil.which('pdftoppm'),'TeX/Poppler integration environment')
    async def test_real_paper_compilation_and_document_review(self):
        await self.plan();await self.solve();await self.accept()
        write(self.root,'paper/coverage.json',{'covered_problem_ids':['q1'],'missing_problem_ids':[]})
        write(self.root,'paper/main.tex',r'\documentclass{article}\begin{document}Finite optimization: $3x+2y\leq3(x+y)\leq12$. Witness $(4,0)$.\end{document}')
        await self.runtime._advance_settled();self.assertEqual(self.w()['mode'],'document_review',self.w())
        images=[p for p in (self.root/'paper/rendered_pages').glob('*.png') if not p.name.endswith('-gray.png')];self.assertTrue(images)
        self.runtime._last_assistant_text=json.dumps(dict(verdict='accept',issues=[],warnings=[],reason='scripted fixture review',evidence_paths=[images[0].relative_to(self.root).as_posix()]))
        await self.runtime._advance_settled();self.assertEqual(self.runtime.status,'completed')
        self.assertIn('paper/main.pdf',self.w()['protected'])

    async def test_worker_has_no_bash_in_balanced_mode(self):
        source=bridge.TOOL_POLICY_EXTENSION.read_text();self.assertIn('MATHMODEL_HOST_COMPUTE',source)
        self.assertIn('"grep", "find", "ls", "edit", "write"',source)


@unittest.skipUnless(shutil.which('xelatex') and shutil.which('pdftoppm'),'TeX/Poppler integration environment')
class RPCProcessTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,hang=False,host_resume=False):
        fixture=BalancedWorkflowTests();fixture.setUp();runtime=fixture.runtime
        if host_resume:
            await fixture.plan();await fixture.solve()
            p=runtime._project();p['workflow']['mode']='compute';runtime._save_project(p)
        for name in ('prompt','system','terminate','_switch_session'):
            setattr(runtime,name,getattr(bridge.TaskRuntime,name).__get__(runtime))
        runtime.requested_model='test/p'
        runtime._safety_state().policy=RuntimePolicy(idle_seconds=2,ack_seconds=2,poll_seconds=.05,
            startup_seconds=15,turn_seconds=40,max_stage_restarts=0,stage_seconds=120,task_seconds=240)
        spawn=asyncio.create_subprocess_exec;which=shutil.which
        async def intercepted(*args,**kwargs):
            if args[0]=='scripted-pi': args=(sys.executable,str(Path(__file__).parent/'fixtures/fake_balanced_rpc.py'))
            return await spawn(*args,**kwargs)
        try:
            with patch('pi.bridge.shutil.which',side_effect=lambda name:'scripted-pi' if name in {'pi','pi.cmd'} else which(name)), \
                 patch('pi.bridge.asyncio.create_subprocess_exec',side_effect=intercepted), \
                 patch.dict(os.environ,{'MMA_FAKE_HANG':'1' if hang else ''}):
                runtime.runner=asyncio.create_task(runtime.run(runtime._v4_prompt(runtime._project())))
                async with asyncio.timeout(60):
                    while runtime.status not in {'completed','completed_with_warnings','partial','failed'}: await asyncio.sleep(.05)
                    if runtime.runner: await runtime.runner
                project=runtime._project()
                self.assertEqual(runtime.status,'partial' if hang else 'completed',project)
                self.assertEqual(project['workflow']['outcomes']['q2' if hang else 'q1']['status'],'accepted')
                self.assertIsNotNone(runtime.process.returncode if runtime.process else 0)
                self.assertEqual(project['runtime_metrics']['prompts'],3 if host_resume else 6 if hang else 5)
                self.assertFalse(project['runtime_metrics']['token_usage_complete'])
                if hang:
                    self.assertTrue(project['runtime_metrics']['faults'])
                    self.assertEqual(project['workflow']['outcomes']['q1']['status'],'unresolved')
                if host_resume: self.assertEqual(project['workflow']['cache_hits'],1)
        finally:
            runtime._stopping=True
            await runtime._stop_runtime();await runtime._safety_fanout().close();fixture.doCleanups()

    async def test_real_process_completes_full_pipeline(self): await self.exercise()
    async def test_ack_only_hang_still_delivers_independent_problem(self): await self.exercise(hang=True)
    async def test_resume_host_compute_avoids_redundant_model_turn(self): await self.exercise(host_resume=True)

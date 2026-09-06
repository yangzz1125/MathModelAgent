import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from pi.tests import test_balanced_workflow as fixtures
from pi.efficient_workflow import verify_project
from scripts.continue_balanced import prepare


class CandidateContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_imports_existing_candidate_and_preserves_source_and_budget(self):
        f=fixtures.BalancedWorkflowTests();f.setUp()
        try:
            await f.plan();await f.solve()
            p=f.runtime._project();p['status']='partial'
            p['workflow']['last_review']=json.loads(fixtures.review(verdict='revise'))
            p['workflow']['mode']='done';p['workflow']['phases'][1]['attempts']=3
            f.runtime._save_project(p)
            original=(f.root/'project.json').read_bytes()
            # Fixture signatures use f*12; use a fresh, matching source directory name.
            with tempfile.TemporaryDirectory() as temp:
                import shutil
                source=Path(temp)/('f'*12)
                shutil.copytree(f.root,source)
                target=Path(temp)/('a'*12)
                profiles={**p['workflow']['profiles'],'document_reviewer':{'model':'test/vision','thinking':'high'}}
                result=prepare(source,target,profiles)
                verify_project(target.name,result)
                self.assertEqual((source/'project.json').read_bytes(),original)
                self.assertEqual(result['workflow']['mode'],'solve')
                self.assertEqual(result['workflow']['phases'][0]['status'],'completed')
                self.assertEqual(result['workflow']['phases'][1]['attempts'],3)
                self.assertEqual(result['runtime_metrics'],p['runtime_metrics'])
                self.assertEqual(result['workflow']['outcomes'],{})
                self.assertTrue((target/'code/q1/solve.py').is_file())
                self.assertFalse((target/'paper/main.tex').exists())
                self.assertIn('Targeted repair',result['workflow']['phases'][1]['last_error'])
                (source/'results/q1/result.json').write_text('{}')
                with self.assertRaises(ValueError): prepare(source,Path(temp)/('b'*12),profiles)
        finally:f.doCleanups()

if __name__=='__main__':unittest.main()

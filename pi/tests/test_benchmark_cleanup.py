"""The opt-in benchmark must use the actual cancellation route and report cleanup."""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from pi import bridge


class BenchmarkCleanupTests(unittest.TestCase):
    def test_deadline_cancels_through_registered_route(self):
        spec = importlib.util.spec_from_file_location('benchmark_pi', Path(__file__).parents[2]/'scripts/benchmark_pi.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for clean in (True, False):
            with self.subTest(clean=clean), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'input.md').write_text('Fixture')
                manifest = root/'case.json'
                manifest.write_text(json.dumps({'cases':[{'id':'fixture','files':['input.md']}]}))
                output = root/'runs.jsonl'
                posts = []
                class Response:
                    def __init__(self, value): self.value = value
                    def raise_for_status(self): pass
                    def json(self): return self.value
                class Client:
                    def __init__(self, **kwargs): pass
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def post(self, path, **kwargs):
                        posts.append(path)
                        return Response({'project_id':'f'*12,'problem_file':'input.md','success':True})
                    def get(self, path):
                        return Response({'status':'cancelled' if clean else 'running', 'runtime_metrics':{}})
                fake_httpx = types.SimpleNamespace(Client=Client, HTTPError=RuntimeError)
                args = ['benchmark',str(manifest),'--execute','--planner-model','test/p',
                        '--worker-model','test/w','--deadline','1','--out',str(output)]
                with patch.dict(sys.modules, {'httpx':fake_httpx}), patch.object(sys,'argv',args), \
                     patch.object(module.time,'monotonic',side_effect=[0,2,3]):
                    module.main()
                cancel = posts[-1]
                self.assertEqual(cancel, '/modeling/'+'f'*12+'/cancel')
                self.assertTrue(any(r.path == '/modeling/{task_id}/cancel' for r in bridge.app.routes))
                record = json.loads(output.read_text())
                self.assertEqual(record['cleanup_confirmed'], clean)
                self.assertEqual(record['execution_status'], 'cancelled' if clean else 'running')
                self.assertTrue(record['hung'])
                if not clean: self.assertIn('cleanup_error', record)


if __name__ == '__main__': unittest.main()

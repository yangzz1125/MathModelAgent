#!/usr/bin/env python3
"""Opt-in real-model benchmark against a LOCAL running Bridge; may spend API credit.

Default: validate inputs only. --execute starts modeling tasks. Credentials remain
in the local Pi installation. Unknown quality/usage is not reported as success/0.
"""
from __future__ import annotations
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlparse
import uuid


def oracle_pass(project, expected):
    if not expected: return None
    outcomes=(project.get('workflow') or {}).get('outcomes',{})
    for pid,metrics in expected.items():
        result=outcomes.get(pid,{})
        if result.get('status')!='accepted': return False
        values={m['name']:m['value'] for m in result.get('metrics',[])}
        for name,value in metrics.items():
            if name not in values or not math.isclose(values[name],value,rel_tol=1e-6,abs_tol=1e-8): return False
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path);p.add_argument('--execute',action='store_true')
    p.add_argument('--base-url',default='http://127.0.0.1:8000')
    p.add_argument('--mode',choices=['balanced','strict'],default='balanced')
    p.add_argument('--planner-model',default='');p.add_argument('--worker-model',default='')
    p.add_argument('--deadline',type=float,default=3600)
    p.add_argument('--out',type=Path,default=Path('benchmark-runs.jsonl'))
    args=p.parse_args()
    if urlparse(args.base_url).hostname not in {'localhost','127.0.0.1','::1'}:
        p.error('The unauthenticated Bridge must remain loopback-only')
    if not math.isfinite(args.deadline) or args.deadline<=0: p.error('Positive finite deadline required')
    try:
        cases=json.loads(args.manifest.read_text(encoding='utf-8'))['cases'];base=args.manifest.resolve().parent
        if not isinstance(cases,list) or not cases: raise ValueError('Nonempty cases required')
        seen=set()
        for case in cases:
            if not isinstance(case.get('id'),str) or not case['id'] or case['id'] in seen or not case.get('files'):
                raise ValueError('Each case needs a unique id and files')
            seen.add(case['id'])
            for name in case['files']:
                if not (base/name).is_file(): raise ValueError('Missing input: '+name)
    except (OSError,ValueError,KeyError,TypeError) as exc: p.error(str(exc))
    if not args.execute:
        print(f'Validated {len(cases)} cases. No API calls. --execute starts paid model tasks.');return
    if not args.planner_model or not args.worker_model: p.error('Explicit model IDs required for reproducibility')
    import httpx
    args.out.parent.mkdir(parents=True,exist_ok=True)
    terminal={'completed','completed_with_warnings','partial','failed','cancelled'}
    with httpx.Client(base_url=args.base_url,timeout=30) as client,args.out.open('a',encoding='utf-8') as log:
        for case in cases:
            started=time.monotonic();task=None;terminal_observed=False
            record=dict(case_id=case['id']+'-'+args.mode+'-'+uuid.uuid4().hex[:8],execution_status='unknown',
                        quality_passed=None,hung=None,mode=args.mode,planner_model=args.planner_model,worker_model=args.worker_model)
            try:
                with contextlib.ExitStack() as stack:
                    uploads=[('files',(Path(name).name,stack.enter_context((base/name).open('rb')),'application/octet-stream')) for name in case['files']]
                    res=client.post('/projects/init',data={'source_folder':case['id']},files=uploads);res.raise_for_status()
                initial=res.json();task=initial['project_id'];record['task_id']=task
                res=client.post(f'/projects/{task}/start',json=dict(workflow_mode=args.mode,problem_file=initial['problem_file'],
                    competition=case.get('competition','MCM'),language=case.get('language','English'),paper_engine=case.get('paper_engine','LaTeX'),
                    planner_model=args.planner_model,worker_model=args.worker_model));res.raise_for_status()
                while time.monotonic()-started<args.deadline:
                    res=client.get(f'/task/{task}/status');res.raise_for_status();state=res.json()
                    if state['status'] in terminal|{'paused','waiting'}:
                        terminal_observed=state['status'] in terminal
                        record.update(execution_status=state['status'],runtime_metrics=state.get('runtime_metrics',{}),
                                      delivery_status=state.get('delivery_status'),hung=False)
                        detail=client.get(f'/projects/{task}');detail.raise_for_status()
                        record['quality_passed']=oracle_pass(detail.json(),case.get('oracle')) if args.mode=='balanced' else None
                        break
                    time.sleep(2)
                else: record.update(error='Benchmark wall deadline exceeded',hung=True)
            except (httpx.HTTPError,ValueError,KeyError,OSError,TypeError) as exc:
                record.update(execution_status='failed',error=str(exc)[:1500])
            finally:
                if task and not terminal_observed:
                    with contextlib.suppress(httpx.HTTPError): client.post(f'/task/{task}/cancel').raise_for_status()
                record['elapsed_seconds']=round(time.monotonic()-started,3)
                log.write(json.dumps(record,allow_nan=False)+'\n');log.flush();os.fsync(log.fileno())
                print(record['case_id'],record['execution_status'])


if __name__=='__main__': main()

"""Continue a verified v4 candidate without changing the terminal source task."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pi.compute_jobs import hashes, safe_path, unchanged
from pi.efficient_contract import plan_contract, result_contract, review_contract
from pi.efficient_workflow import EVIDENCE_DIRS, sign_project, verify_project


def prepare(source: Path, destination: Path, profiles: dict) -> dict:
    if not re.fullmatch(r'[a-f0-9]{12}', destination.name):
        raise ValueError('Continuation requires a new task id')
    original = (source/'project.json').read_bytes()
    project = json.loads(original)
    verify_project(source.name, project)
    w = project['workflow']
    if project['status'] not in {'partial','failed'} or w['contract_version'] != 4:
        raise ValueError('Expected terminal v4 candidate')
    if project.get('runtime_metrics',{}).get('cleanup_required') or w.get('pending_transition'):
        raise ValueError('Unconfirmed source cleanup/transition')
    if w.get('paper_passed') or w.get('outcomes',{}).get('q1',{}).get('status') == 'accepted':
        raise ValueError('This continuation imports an unaccepted candidate only')
    if [p['id'] for p in plan_contract({'problems':w['plan']})] != ['q1']:
        raise ValueError('Candidate continuation currently supports one existing question')
    before = hashes(source, EVIDENCE_DIRS)
    if hashes(source,w['protected_roots']) != w['protected']:
        raise ValueError('Frozen source evidence changed')
    receipt = w['job_receipts']['q1']
    if not unchanged(source,receipt['source_hashes']) or not unchanged(source,receipt['output_hashes']):
        raise ValueError('Candidate receipt changed')
    if hashes(source,['code/q1','input','input_manifest.json']) != receipt['source_hashes']:
        raise ValueError('Unexpected source dependency')
    if hashes(source,['results/q1','figures/q1']) != receipt['output_hashes']:
        raise ValueError('Unexpected candidate output')
    result_contract(source,'q1')
    review = w.get('last_review')
    review_source = None
    if not review:
        messages = json.loads((source/'.pi-bridge/messages.json').read_text(encoding='utf-8'))
        for message in reversed(messages):
            try: candidate = review_contract(message.get('content',''))
            except (ValueError,TypeError): continue
            if candidate['verdict'] in {'revise','reject'}:
                review = candidate
                review_source = {'file':'.pi-bridge/messages.json','message_id':message['id'],
                                 'sha256':hashlib.sha256((source/'.pi-bridge/messages.json').read_bytes()).hexdigest()}
                break
    if not review:
        for session in sorted((source/'.pi-sessions').glob('*.jsonl')):
            raw_session = session.read_bytes()
            for line in raw_session.decode('utf-8').splitlines():
                entry = json.loads(line)
                message = entry.get('message') or {}
                if message.get('role') != 'assistant': continue
                content = '\n'.join(block['text'] for block in message.get('content',[])
                                    if block.get('type') == 'text')
                try: candidate = review_contract(content)
                except (ValueError,TypeError): continue
                if candidate['verdict'] in {'revise','reject'}:
                    review = candidate
                    review_source = {'file':session.relative_to(source).as_posix(),
                                     'entry_id':entry.get('id'),
                                     'sha256':hashlib.sha256(raw_session).hexdigest()}
    if not review or review['verdict'] != 'revise':
        raise ValueError('A recorded actionable revise verdict is required')
    review_contract(json.dumps(review))
    for name in review['evidence_paths']: safe_path(source,name).stat()
    now = datetime.now(timezone.utc).isoformat()
    destination.mkdir(parents=True,exist_ok=False)
    for directory in ('input','planning','code','results','figures'):
        if (source/directory).exists():
            shutil.copytree(source/directory,destination/directory)
        else:
            (destination/directory).mkdir()
    shutil.copy2(source/'input_manifest.json',destination/'input_manifest.json')
    (destination/'reports').mkdir()
    (destination/'paper').mkdir()
    if (source/'project.json').read_bytes() != original or hashes(source,EVIDENCE_DIRS) != before:
        raise ValueError('Source changed during continuation import; do not start')
    if review_source and hashlib.sha256((source/review_source['file']).read_bytes()).hexdigest() != review_source['sha256']:
        raise ValueError('Review source changed during import')
    result_contract(destination,'q1')
    if not unchanged(destination,receipt['source_hashes']) or not unchanged(destination,receipt['output_hashes']):
        raise ValueError('Copied receipt does not match')
    continued = {k:copy.deepcopy(project[k]) for k in (
        'problem_file','source_folder','competition','language','paper_engine',
        'user_notes','user_requirements_file','runtime_metrics') if k in project}
    continued.update(project_id=destination.name,status='paused',created_at=now,started_at=now,
                     pause_reason='user_authorized_candidate_continuation')
    continued['continuation_source'] = {
        'project_id':source.name,'status':project['status'],'restart_stage':'problem:q1/solve',
        'source_project_sha256':hashlib.sha256(original).hexdigest(),
        'source_profiles':w['profiles'],'source_runtime_metrics':project.get('runtime_metrics',{}),
        'source_outcomes':w['outcomes'],'source_phases':w['phases'],
        'imported_hashes':hashes(destination,EVIDENCE_DIRS),'review_source':review_source,'imported_at':now}
    current = copy.deepcopy(w)
    current.update(current='problem:q1',mode='solve',outcomes={},last_review=review,profiles=profiles,
                   candidate_continuation=True)
    for phase in current['phases']:
        if phase['id']=='problem:q1':
            phase.update(status='paused',status_before_pause='running',
                         last_error='Targeted repair of recorded review: '+review['reason']+'; '+'; '.join(review['issues']))
        elif phase['id'] in {'writing','verify'}:
            phase['status']='pending'
            phase.pop('last_error',None)
    current['snapshot']=hashes(destination,EVIDENCE_DIRS)
    continued['workflow']=current
    continued.update(planner_model=profiles['planner']['model'],worker_model=profiles['worker']['model'],
                     model=profiles['worker']['model'],thinking=profiles['worker']['thinking'])
    sign_project(destination.name,continued)
    (destination/'project.json').write_text(json.dumps(continued,ensure_ascii=False,indent=2),encoding='utf-8')
    return continued

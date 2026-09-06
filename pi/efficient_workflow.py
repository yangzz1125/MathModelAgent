"""Balanced v4: plan -> solve/Host compute/review -> write/compile/review.

Finite repairs, signed checkpoints, exact frozen evidence sets, truthful partial
outcomes. Legacy v1-v3 remain on their original engine. This is not a sandbox.
"""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import hmac
import json
import shutil
import sys
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from pi.compute_jobs import HostCleanupError, hashes, run_job, safe_path, unchanged
from pi.efficient_contract import load_object, plan_contract, result_contract, review_contract, strings

TERMINAL = {'completed','completed_with_warnings','partial','failed','cancelled','paused'}
EVIDENCE_DIRS = ('input','input_manifest.json','planning','code','results','figures','paper','reports','todo.md')


def initial_balanced_workflow(profiles: dict[str, Any], root: Path) -> dict[str, Any]:
    return {'contract_version':4, 'profiles':profiles, 'current':'planning', 'mode':'plan',
            'plan_version':1, 'plan':[], 'outcomes':{}, 'warnings':[], 'paper_passed':False,
            'phases':[{'id':'planning','label':'Plan robust methods','status':'running','attempts':1}],
            'protected_roots':['input','input_manifest.json'],
            'protected':hashes(root,['input','input_manifest.json']),
            'snapshot':hashes(root,EVIDENCE_DIRS)}


def sign_project(task_id, project):
    from pi.bridge import _transition_signature
    project.pop('_host_signature',None)
    project['_host_signature']=_transition_signature(task_id,project)


def verify_project(task_id, project):
    from pi.bridge import _transition_signature
    value=dict(project); signature=value.pop('_host_signature','')
    if not isinstance(signature,str) or not hmac.compare_digest(signature,_transition_signature(task_id,value)):
        raise ValueError('artifact_changed: Host workflow state signature mismatch')


class EfficientWorkflowMixin:
    def _v4_phase(self,w):
        return next(p for p in w['phases'] if p['id']==w['current'])

    def _v4_problem(self,w):
        pid=w['current'].split(':',1)[-1]
        return next((p for p in w['plan'] if p['id']==pid),None)

    def _v4_profile(self,w):
        if w['mode']=='document_review' and 'document_reviewer' in w['profiles']:
            return 'document_reviewer'
        return 'planner' if w['mode'] in {'plan','scientific_review','document_review'} else 'worker'

    def _v4_prompt(self,project):
        w=project['workflow']; mode=w['mode']; p=self._v4_problem(w)
        prefix=(
            'Balanced v4 protocol: follow ONLY this stage, not legacy v3 Skill scaffolding. '
            'Never edit project.json, planning/ledger.json, .pi-bridge, inputs, or other questions. '
            'Host owns execution, budgets, acceptance, and transitions. Finish with one concise handoff.\n'
            f"Language: {project.get('language','English')}; competition: {project.get('competition','')}.\n"
            f"Current stage: {w['current']}; mode: {mode}.\n"
            f"Read original problem: {project['problem_file']}; data index: input_manifest.json.\n"
            f"User requirements: {project.get('user_requirements_file') or '(none)'}; {str(project.get('user_notes') or '')[:4000]}\n"
            'Latest feedback: '+str(self._v4_phase(w).get('last_error') or '')[-6000:]+'\n')
        if mode=='write' and w.get('paper_layout'):
            from pi.staged_workflow import PAPER_LAYOUT_CONTRACT
            prefix += PAPER_LAYOUT_CONTRACT + '\n'
        if mode=='plan':
            return prefix+(
                'Read every requested output and the available input data. Write planning/plan.json: '
                '{"problems":[{"id":"q1","question":"exact requested output",'
                '"depends_on":[],"method":"simple reliable method","fallback":"simpler valid alternative",'
                '"acceptance":["specific feasibility/correctness check"],"runtime_seconds":180}]}. '
                'Cover ALL original questions, at most 20, and use dependencies only where required. '
                'Budget 1..600 seconds per solve. Do not invent data. Method selection, feasibility '
                'assessment and fallback are internal planning, not separate mandatory stages. '
                'Prefer a reliable baseline over a fragile elaborate model.')
        if mode=='solve':
            pid=p['id']; fallback=self._v4_phase(w)['attempts']>=3 and not w.get('candidate_continuation')
            deps={d:w['outcomes'][d] for d in p['depends_on']}
            return prefix+json.dumps(p,ensure_ascii=False)+'\n'+(
                ('Continue the existing candidate. Read its code and recorded feedback, make targeted corrections only. '
                 'Do not restart planning, replace the working method, or write the manuscript in solve.py. '
                 'Host will rerun changed code and retain unchanged evidence.\n' if w.get('candidate_continuation') else '')+
                ('Use the FALLBACK now; do not repeat the failed method.\n' if fallback else '')+
                f'Accepted dependencies: {json.dumps(deps,ensure_ascii=False)}\n'
                f'Only write code/{pid}/, results/{pid}/, figures/{pid}/. '
                f'Create code/{pid}/solve.py and code/{pid}/validate.py. Host runs both '
                'with its Python in the workspace root; no bash or package installs. '
                'Do not spawn background jobs. Use numpy/scipy/pandas/matplotlib where useful. '
                f'solve.py must produce results/{pid}/result.json and requested data/tables/figures. '
                '{"problem_id":"'+pid+'","status":"candidate","metrics":[{"name":"objective",'
                '"value":1.0,"unit":"..."}],"assumptions":[],"limitations":[],"figures":[]}. '
                'The numeric schema example is NOT an answer. Compute ALL results from real inputs. '
                f'validate.py must independently read computed evidence and produce results/{pid}/validation.json: '
                '{"independent_method":"separate checking logic",'
                '"checks":[{"name":"feasibility","passed":true,"detail":"numeric tolerance/error"}]}. '
                'Never hardcode passed=true or import the solver as your independent validator. '
                'Explicitly evaluate constraints, tolerances and objective/bounds. Fail false when invalid. '
                'Heuristic results are not global optimality proofs. Declare assumptions and limitations. '
                'Every requested figure must be declared with its actual figures/qN/ path.')
        if mode in {'scientific_review','document_review'}:
            scope=(f"Original problem plus {json.dumps(p,ensure_ascii=False)}; inspect code/{p['id']}/, results/{p['id']}/, figures/{p['id']}/."
                   if p else 'Read original problem, accepted evidence, paper source, and EVERY physical page image in paper/rendered_pages/.')
            return prefix+scope+'\n'+(
                'You are an independent READ-ONLY reviewer; never run or modify files. '
                'Check original-question coverage, method correctness, constraints, numeric consistency, '
                'independence of validation, assumptions and unsupported claims. For document review '
                'also verify all page images, readability and honest treatment of unresolved questions. '
                'Approximation is allowed only when the question permits it and its limitations are explicit. '
                'Hard errors, omitted requested outputs or unjustified conclusions require revise/reject. '
                'Return ONLY {"verdict":"accept|revise|reject","issues":[],"warnings":[], '
                '"reason":"explain judgment","evidence_paths":["actual inspected file"]}. '
                'Accept requires no unresolved issues; other verdicts require actionable issues. '
                'List real inspected evidence, never invented paths.')
        if mode=='write':
            return prefix+f"Outcomes: {json.dumps(w['outcomes'],ensure_ascii=False)}\n"+(
                f"Paper engine: {project.get('paper_engine','LaTeX')}. Only write paper/ and reports/. "
                'Create paper/main.tex or main.typ, using relative paths. Host compiles; do not compile yourself. '
                'Use portable article/ctexart and no shell escape. Write substantive equations, methods, '
                'computed results, validation, analysis, limitations and truthful citations. '
                'Only accepted results may support scientific conclusions. Label rejected/raw/unresolved '
                'results explicitly; do not present them as solved. Reference original inputs and accepted '
                'results/qN/ artifacts, reuse their figures. No separate conceptual diagram stage is required. '
                'Write paper/coverage.json: {"covered_problem_ids":[all accepted IDs],'
                '"missing_problem_ids":[all other IDs]}. Include every original requested question in the paper.')
        return prefix+'Host operation pending; no additional Agent turn is needed.'

    def _v4_check_scope(self,w,*,review=False,compute=False):
        if hashes(self.workspace,w['protected_roots'])!=w['protected']:
            raise ValueError('artifact_changed: input or frozen evidence changed')
        current=hashes(self.workspace,EVIDENCE_DIRS); previous=w.get('snapshot',{})
        changed={p for p in current.keys()|previous.keys() if current.get(p)!=previous.get(p)}
        p=self._v4_problem(w)
        if review: allowed=()
        elif p:
            allowed=(f"results/{p['id']}/",f"figures/{p['id']}/")
            if not compute: allowed+=(f"code/{p['id']}/",)
        elif w['mode']=='plan': allowed=('planning/plan.json',)
        else: allowed=('paper/','reports/')
        illegal=sorted(p for p in changed if not any(p==a or (a.endswith('/') and p.startswith(a)) for a in allowed))
        if illegal:
            raise ValueError('artifact_changed: stage wrote outside scope: '+', '.join(illegal[:10]))
        return current

    async def _v4_begin(self,*,new_session=True,host=False):
        if self._stopping: return
        project=self._project(); w=project['workflow']; phase=self._v4_phase(w)
        phase['status']='running'; phase.setdefault('attempts',1)
        if w['mode']=='write' and w.get('paper_layout'):
            from pi.paper_layout import LAYOUT_SOURCES
            target=safe_path(self.workspace,'paper/cumcm-layout.tex')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(LAYOUT_SOURCES[w['paper_layout']].read_bytes())
        w['snapshot']=hashes(self.workspace,EVIDENCE_DIRS)
        project['status']='running'; self._save_project(project); self.status='running'
        if host:
            await self._v4_host_step(); return
        if new_session: await self._switch_session(self._v4_profile(w))
        if self._stopping: return
        await self.system('Stage: '+w['current']+' / '+w['mode'])
        await self.prompt(self._v4_prompt(self._project()))

    async def _advance_settled(self):
        if self._stopping or self.status in TERMINAL:
            return
        if not (self.workspace / 'project.json').is_file():
            return await self._advance_legacy_settled()
        if (self._project().get('workflow') or {}).get('contract_version')!=4:
            return await self._advance_legacy_settled()
        async with self._transition_lock:
            if self._stopping or self.status in TERMINAL: return
            project=self._project(); w=project['workflow']; mode=w['mode']
            try: self._v4_check_scope(w,review=mode in {'scientific_review','document_review'})
            except (ValueError,OSError) as exc:
                await self._v4_finish(str(exc),integrity=True); return
            try:
                if mode=='plan':
                    w['plan']=plan_contract(load_object(self.workspace,'planning/plan.json'))
                    self._v4_phase(w)['status']='completed'
                    w['protected_roots'].append('planning/plan.json')
                    w['protected'].update(hashes(self.workspace,['planning/plan.json']))
                    w['phases'] += [{'id':'problem:'+p['id'],'label':p['question'][:160],'status':'pending','attempts':0} for p in w['plan']]
                    w['phases'] += [{'id':'writing','label':'Paper and Host compilation','status':'pending','attempts':0},
                                    {'id':'verify','label':'Document review','status':'pending','attempts':0}]
                    self._save_project(project); await self._v4_next()
                elif mode=='solve':
                    p=self._v4_problem(w)
                    for name in ('solve.py','validate.py'):
                        if not safe_path(self.workspace,f"code/{p['id']}/{name}").is_file():
                            raise ValueError('Missing '+name)
                    w['mode']='compute'; self._save_project(project)
                    await self._v4_begin(new_session=False,host=True)
                elif mode in {'scientific_review','document_review'}:
                    await self._v4_review(project)
                elif mode=='write':
                    from pi.paper_layout import paper_layout_errors
                    layout_errors=paper_layout_errors(self.workspace,w.get('paper_layout'))
                    if layout_errors: raise ValueError('; '.join(layout_errors))
                    accepted={pid for pid,r in w['outcomes'].items() if r['status']=='accepted'}
                    coverage=load_object(self.workspace,'paper/coverage.json')
                    if set(strings(coverage.get('covered_problem_ids'),'covered_problem_ids'))!=accepted:
                        raise ValueError('Paper coverage must exactly match accepted problems')
                    if set(strings(coverage.get('missing_problem_ids'),'missing_problem_ids'))!=set(w['outcomes'])-accepted:
                        raise ValueError('Paper must identify unresolved questions')
                    w['mode']='compile'; self._save_project(project)
                    await self._v4_begin(new_session=False,host=True)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                await self._v4_repair(str(exc))

    async def _v4_resume_host(self):
        # A separate tracked transition lets the stdout reader answer session RPCs.
        async with self._transition_lock:
            if not self._stopping: await self._v4_host_step()

    async def _v4_host_step(self):
        project=self._project(); w=project['workflow']; phase=self._v4_phase(w)
        if self._stopping: return
        self._host_job_active=True
        try:
            if w['mode']=='compute':
                p=self._v4_problem(w); pid=p['id']; seconds=p['runtime_seconds']
                self._host_operation_deadline=time.monotonic()+seconds+min(seconds,120)+15
                versions={}
                for package in ('numpy','scipy','pandas','matplotlib'):
                    try: versions[package]=version(package)
                    except PackageNotFoundError: versions[package]='absent'
                environment={'python':sys.version,'executable':sys.executable,'packages':versions}
                source_roots=[f'code/{pid}','input','input_manifest.json']+[f'results/{d}' for d in p['depends_on']]
                output_roots=[f'results/{pid}',f'figures/{pid}']
                self._v4_check_scope(w,compute=True)
                source_hashes=hashes(self.workspace,source_roots)
                receipt=w.get('job_receipts',{}).get(pid)
                cached=bool(receipt and receipt.get('environment')==environment and receipt['source_hashes']==source_hashes
                            and hashes(self.workspace,output_roots)==receipt['output_hashes'])
                if cached: w['cache_hits']=w.get('cache_hits',0)+1
                else:
                    for name in ('result.json','validation.json'):
                        safe_path(self.workspace,f'results/{pid}/{name}').unlink(missing_ok=True)
                    records=[]
                    for name in ('solve.py','validate.py'):
                        script=safe_path(self.workspace,f'code/{pid}/{name}')
                        result=await run_job([sys.executable,str(script)],self.workspace,
                                             seconds=seconds if name=='solve.py' else min(seconds,120))
                        records.append(result.record())
                        if self._stopping: return
                        self._v4_check_scope(w,compute=True)
                        if result.timed_out or result.returncode:
                            raise ValueError(f'{name}: timeout={result.timed_out}, exit={result.returncode}\n{result.output}')
                    if hashes(self.workspace,source_roots)!=source_hashes:
                        raise ValueError('artifact_changed: code/input changed during computation')
                    result_contract(self.workspace,pid)
                    receipt={'source_hashes':source_hashes,'environment':environment,
                             'output_hashes':hashes(self.workspace,output_roots),'jobs':records}
                    w.setdefault('job_receipts',{})[pid]=receipt
                    w['compute_jobs']=w.get('compute_jobs',0)+len(records)
                result_contract(self.workspace,pid)
                w['mode']='scientific_review'; phase['review_status']='running'
                self._save_project(project); await self._v4_begin()
            elif w['mode']=='compile':
                self._host_operation_deadline=time.monotonic()+400
                paper=safe_path(self.workspace,'paper'); engine=str(project.get('paper_engine','LaTeX')).lower()
                compiler=shutil.which('xelatex' if engine=='latex' else 'typst')
                if not compiler or not shutil.which('pdfinfo') or not shutil.which('pdftoppm'):
                    await self._v4_finish('Document tools unavailable; validated results retained'); return
                source=safe_path(self.workspace,'paper/main.tex' if engine=='latex' else 'paper/main.typ')
                if not source.is_file(): raise ValueError('Missing paper source')
                safe_path(self.workspace,'paper/main.pdf').unlink(missing_ok=True)
                command=([compiler,'-no-shell-escape','-interaction=nonstopmode','-halt-on-error','main.tex'] if engine=='latex'
                         else [compiler,'compile','--root',str(self.workspace),'main.typ','main.pdf'])
                records=[]
                for _ in range(2 if engine=='latex' else 1):
                    result=await run_job(command,paper,seconds=120); records.append(result.record())
                    if result.timed_out or result.returncode: raise ValueError('Paper compilation failed: '+result.output)
                pdf=safe_path(self.workspace,'paper/main.pdf')
                if not pdf.is_file() or not pdf.stat().st_size: raise ValueError('Compiler produced no PDF')
                from pi.paper_evidence import render_paper_pages
                self._host_render_cancel=threading.Event()
                try:
                    w['paper_visual']=await asyncio.to_thread(render_paper_pages,self.workspace,pdf,
                                                             cancelled=self._host_render_cancel.is_set)
                finally: self._host_render_cancel=None
                if self._stopping: return
                self._v4_check_scope(w); w['paper_build']=records; phase['status']='completed'
                w.update(current='verify',mode='document_review')
                self._save_project(project); await self._v4_begin()
        except HostCleanupError as exc:
            self._safety_state().host_cleanup_error = exc
            self._mark_cleanup_unconfirmed(str(exc))
            with contextlib.suppress(Exception):
                await self.terminate()
        except (ValueError,OSError,KeyError,TypeError) as exc:
            if 'artifact_changed:' in str(exc): await self._v4_finish(str(exc),integrity=True)
            elif not self._stopping: await self._v4_repair(str(exc))
        finally:
            self._host_job_active=False; self._host_operation_deadline=None

    async def _v4_review(self,project):
        w=project['workflow']; phase=self._v4_phase(w)
        try:
            review=review_contract(self._last_assistant_text)
            for path in review['evidence_paths']:
                if not safe_path(self.workspace,path).is_file(): raise ValueError('Missing review evidence: '+path)
            if w['mode']=='document_review' and review['verdict']=='accept':
                from pi.paper_evidence import paper_visual_errors
                errors=paper_visual_errors(self.workspace,w.get('paper_visual'))
                if errors:
                    await self._v4_finish('; '.join(errors),integrity=True)
                    return
                required={name for name in w['paper_visual']['files'] if name.endswith('.png') and not name.endswith('-gray.png')}
                if not required.issubset(set(review['evidence_paths'])):
                    raise ValueError('Document acceptance must identify every physical color page as inspected')
        except (ValueError,OSError) as exc:
            phase['protocol_attempts']=phase.get('protocol_attempts',0)+1
            phase['last_error']='Review JSON only: '+str(exc); self._save_project(project)
            if phase['protocol_attempts']<=1: await self._v4_begin(new_session=False)
            elif w['mode']=='document_review': await self._v4_finish('Document review unavailable; paper is unverified')
            else: await self._v4_skip('Independent review unavailable; result remains unverified')
            return
        w['last_review']=review
        self._save_project(project)
        if review['verdict']!='accept':
            await self._v4_repair('Review: '+review['reason']+'; '+'; '.join(review['issues'])); return
        w['warnings'].extend(review['warnings']); phase['review_status']='accepted'
        if w['mode']=='document_review':
            w['paper_passed']=True; w['document_review']=review
            w['protected_roots'].append('paper'); w['protected'].update(hashes(self.workspace,['paper']))
            phase['status']='completed'; self._save_project(project); await self._v4_finish()
        else:
            p=self._v4_problem(w); pid=p['id']; receipt=w['job_receipts'][pid]
            if not unchanged(self.workspace,receipt['source_hashes']) or not unchanged(self.workspace,receipt['output_hashes']):
                await self._v4_finish('artifact_changed: compute receipt changed',integrity=True); return
            value=result_contract(self.workspace,pid)
            roots=[f'code/{pid}',f'results/{pid}',f'figures/{pid}']
            w['protected_roots'].extend(roots); w['protected'].update(hashes(self.workspace,roots))
            w['outcomes'][pid]={'status':'accepted','result':f'results/{pid}/result.json','metrics':value['metrics'],'review':review}
            phase['status']='completed'; self._save_project(project); await self._v4_next()

    async def _v4_repair(self,error):
        if error.startswith('artifact_changed:'):
            await self._v4_finish(error,integrity=True); return
        project=self._project(); w=project['workflow']; phase=self._v4_phase(w); prior=w['mode']
        if prior=='document_review':
            phase['status']='pending'; w.update(current='writing',mode='write'); phase=self._v4_phase(w)
        attempts=max(1,phase.get('attempts',1)); cap=3 if self._v4_problem(w) else 2
        fingerprint=hashlib.sha256(error.encode()).hexdigest(); repeats=phase.setdefault('failure_fingerprints',{})
        repeats[fingerprint]=repeats.get(fingerprint,0)+1; phase['last_error']=error[-6000:]
        if attempts>=cap:
            self._save_project(project)
            if self._v4_problem(w): await self._v4_skip(error)
            else: await self._v4_finish(error)
            return
        phase['attempts']=cap if repeats[fingerprint]>1 else attempts+1; phase['protocol_attempts']=0
        w['mode']='solve' if self._v4_problem(w) else ('plan' if w['current']=='planning' else 'write')
        self._save_project(project)
        await self._v4_begin(new_session=prior in {'scientific_review','document_review'})

    async def _v4_skip(self,error,*,start=True):
        project=self._project(); w=project['workflow']; p=self._v4_problem(w)
        self._v4_phase(w).update(status='failed',last_error=error[-6000:])
        w['outcomes'][p['id']]={'status':'unresolved','reason':error[-2000:]}
        self._save_project(project); await self._v4_next(start=start)

    async def _v4_next(self,*,start=True):
        project=self._project(); w=project['workflow']
        for p in w['plan']:
            if p['id'] in w['outcomes']: continue
            w.update(current='problem:'+p['id'],mode='solve'); phase=self._v4_phase(w)
            blocked=[d for d in p['depends_on'] if w['outcomes'].get(d,{}).get('status')!='accepted']
            if blocked:
                reason='Unresolved dependency: '+', '.join(blocked)
                w['outcomes'][p['id']]={'status':'blocked','reason':reason}; phase.update(status='failed',last_error=reason)
                continue
            phase['attempts']=max(1,phase.get('attempts',0)); self._save_project(project)
            if start: await self._v4_begin()
            return
        if not any(o['status']=='accepted' for o in w['outcomes'].values()):
            self._save_project(project); await self._v4_finish('No independently accepted problem results'); return
        w.update(current='writing',mode='write'); self._v4_phase(w)['attempts']=max(1,self._v4_phase(w).get('attempts',0))
        self._save_project(project)
        if start: await self._v4_begin()

    async def _finalize_runtime_failure(self,error):
        project=self._project(); w=project.get('workflow') or {}
        project['runtime_failure']=error[:2000]
        self._save_project(project)
        if w.get('contract_version')!=4:
            return await super()._finalize_runtime_failure(error)
        # Called only after the previous process and Host operations are stopped.
        global_error=any(t in error.lower() for t in ('task_budget','task_prompt_budget','artifact_changed','authentication','invalid api key','missing credentials','no api key','unauthorized','permission denied','cleanup','provider_error:'))
        if self._v4_problem(w) and not global_error:
            await self._v4_skip(error,start=False)
            if self.status not in {'failed','partial','completed','completed_with_warnings'}:
                project=self._project();project['status']='paused';self.status='paused';self._save_project(project)
                await self._resume()
        else:
            await self._v4_finish(error,integrity='artifact_changed' in error)

    async def _v4_finish(self,reason='',*,integrity=False):
        project=self._project();w=project['workflow']
        try:
            if hashes(self.workspace,w['protected_roots'])!=w['protected']:
                integrity=True;reason='Frozen input/evidence changed; prior acceptance cannot be relied upon.'
        except (OSError,ValueError): integrity=True;reason='Frozen evidence is inaccessible or unsafe.'
        all_done=bool(w['plan']) and all(w['outcomes'].get(p['id'],{}).get('status')=='accepted' for p in w['plan'])
        if integrity: status='failed'
        elif all_done and w['paper_passed'] and not reason: status='completed_with_warnings' if w['warnings'] else 'completed'
        else:
            meaningful=any(o.get('status')=='accepted' for o in w['outcomes'].values()) or any(
                (self.workspace/d).is_dir() and any(p.is_file() and p.stat().st_size for p in (self.workspace/d).rglob('*')) for d in ('results','paper'))
            status='partial' if meaningful else 'failed'
        w['mode']='done'
        if reason and self._v4_phase(w)['status']!='completed': self._v4_phase(w).update(status='failed',last_error=reason[-2000:])
        for phase in w['phases']:
            if phase['status']=='pending': phase.update(status='failed',last_error='Not reached before bounded termination')
        project.update(status=status,delivery_status=status,delivery_reason=reason)
        self._save_project(project); self.status=status
        reports=safe_path(self.workspace,'reports');reports.mkdir(exist_ok=True)
        lines=['# MathModelAgent delivery','','Status: '+status,'',reason,'',
               'Only independently accepted results count as solved. Raw files are not certification.','']
        for p in w['plan']:
            o=w['outcomes'].get(p['id'],{'status':'unresolved'})
            lines += [f"## {p['id']}: {p['question']}",f"Status: {o['status']}",json.dumps(o.get('metrics',[]),ensure_ascii=False),o.get('reason',''),'']
        lines += ['## Warnings',*w['warnings']]
        safe_path(self.workspace,'reports/DELIVERY.md').write_text('\n'.join(lines),encoding='utf-8')
        await self.system('Delivery: '+status,'success' if status.startswith('completed') else 'warning')
        await self.terminate()

"""Small semantic contracts for balanced v4. Normalization never invents evidence."""
from __future__ import annotations
import json
import math
import re
from pathlib import Path
from typing import Any
from pi.compute_jobs import safe_path
from pi.runtime_support import normalize_json_envelope


def object_json(text: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            invalid(value)
        return result
    result = json.loads(normalize_json_envelope(text), object_pairs_hook=pairs,
                        parse_constant=invalid, parse_float=finite_float)
    if not isinstance(result, dict):
        raise ValueError('Expected one JSON object')
    return result


def load_object(root: Path, relative: str) -> dict[str, Any]:
    path = safe_path(root, relative)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Protocol artifact exceeds 2 MB; store data separately')
    return object_json(path.read_text(encoding='utf-8-sig'))


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value.strip()


def strings(value, label, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(label + ' must be a list' + (' with entries' if nonempty else ''))
    return [text(item, label) for item in value]


def plan_contract(raw):
    problems = raw.get('problems')
    if not isinstance(problems, list) or not 1 <= len(problems) <= 20:
        raise ValueError('Plan must contain 1..20 problems')
    by_id = {}
    for p in problems:
        if not isinstance(p, dict):
            raise ValueError('Each problem must be an object')
        pid = text(p.get('id'), 'id')
        if not re.fullmatch(r'q[1-9][0-9]*', pid) or pid in by_id:
            raise ValueError('Problem id must be unique and match q1, q2, ...')
        seconds = p.get('runtime_seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, (float,int)) or not 1 <= seconds <= 600:
            raise ValueError('runtime_seconds must be finite in 1..600')
        by_id[pid] = {'id':pid, **{k:text(p.get(k),k) for k in ('question','method','fallback')},
                      'depends_on':strings(p.get('depends_on'),'depends_on'),
                      'acceptance':strings(p.get('acceptance'),'acceptance',nonempty=True),
                      'runtime_seconds':seconds}
    for p in by_id.values():
        if len(set(p['depends_on'])) != len(p['depends_on']) or any(d not in by_id or d==p['id'] for d in p['depends_on']):
            raise ValueError('Invalid dependency')
    ordered=[]; seen=set()
    while len(ordered)<len(by_id):
        ready=[p for p in by_id.values() if p['id'] not in seen and set(p['depends_on']) <= seen]
        if not ready:
            raise ValueError('Cyclic dependencies')
        ordered.extend(ready); seen.update(p['id'] for p in ready)
    return ordered


def result_contract(root: Path, pid: str):
    result=load_object(root, f'results/{pid}/result.json')
    if result.get('problem_id') != pid or result.get('status') != 'candidate':
        raise ValueError('Expected candidate for ' + pid + ', not self-accepted output')
    metrics=result.get('metrics'); names=set()
    if not isinstance(metrics,list) or not metrics:
        raise ValueError('No numeric result metrics')
    for m in metrics:
        if not isinstance(m,dict): raise ValueError('Metric must be an object')
        name=text(m.get('name'),'metric name'); value=m.get('value')
        if name in names or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Metric must be unique and have a finite numeric value')
        names.add(name)
    for key in ('assumptions','limitations'):
        strings(result.get(key),key)
    for figure in strings(result.get('figures'),'figures'):
        if not safe_path(root,figure,prefix=f'figures/{pid}').is_file():
            raise ValueError('Missing declared figure')
    validation=load_object(root, f'results/{pid}/validation.json')
    text(validation.get('independent_method'),'independent_method')
    checks=validation.get('checks')
    if not isinstance(checks,list) or not checks:
        raise ValueError('No independent validation checks')
    for check in checks:
        if not isinstance(check,dict) or check.get('passed') is not True:
            raise ValueError('Independent numerical check failed: ' + str(check)[:500])
        text(check.get('name'),'check name')
    return result


def review_contract(content):
    r=object_json(content)
    if r.get('verdict') not in {'accept','revise','reject'}:
        raise ValueError('Invalid review verdict')
    for k in ('issues','warnings'):
        strings(r.get(k),k)
    text(r.get('reason'),'reason')
    strings(r.get('evidence_paths'),'evidence_paths',nonempty=True)
    if r['verdict']=='accept' and r['issues']:
        raise ValueError('Cannot accept with unresolved issues')
    if r['verdict']!='accept' and not r['issues']:
        raise ValueError('Rejected review needs actionable issues')
    return r

"""Scripted RPC peer; generates solver CODE, never mocked numeric job outputs."""
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0,os.environ['MATHMODELAGENT_ROOT'])
from pi.tests.test_balanced_workflow import item,write,solver,review
root=Path.cwd()
def emit(value): print(json.dumps(value),flush=True)
for line in sys.stdin:
    c=json.loads(line)
    emit({'type':'response','id':c.get('id'),'command':c['type'],'success':True,'data':{}})
    if c['type']!='prompt' or c.get('message','').startswith('/mathmodel-tool-policy'): continue
    stage,mode=re.search(r'Current stage: ([^;]+); mode: ([^.]+)\.',c['message']).groups()
    if os.environ.get('MMA_FAKE_HANG')=='1' and stage=='problem:q1': continue
    content='Candidate files ready for Host validation.'
    if mode=='plan': write(root,'planning/plan.json',{'problems':[item(),item('q2')] if os.environ.get('MMA_FAKE_HANG') else [item()]})
    elif mode=='solve': solver(root,stage.split(':')[1])
    elif mode=='scientific_review': content=review(stage.split(':')[1])
    elif mode=='write':
        write(root,'paper/coverage.json',{'covered_problem_ids':['q2'] if os.environ.get('MMA_FAKE_HANG') else ['q1'],'missing_problem_ids':['q1'] if os.environ.get('MMA_FAKE_HANG') else []})
        write(root,'paper/main.tex',r'''\documentclass{article}
\begin{document}
\section{Model} Integer $x,y\geq0$, $x+y\leq4$. Maximize $3x+2y$.
\section{Result} Enumeration gives $(4,0)$ and objective 12.
\section{Independent validation} $3x+2y\leq3(x+y)\leq12$; feasible witness attains 12.
\section{Limitations} Finite domain only. Unresolved questions are listed in the delivery report.
\end{document}''')
    elif mode=='document_review':
        pages=[p for p in (root/'paper/rendered_pages').glob('*.png') if not p.name.endswith('-gray.png')]
        content=json.dumps(dict(verdict='accept',issues=[],warnings=[],reason='scripted regression review',evidence_paths=[p.relative_to(root).as_posix() for p in pages]))
    else: raise ValueError('Unexpected Agent mode: '+mode)
    emit({'type':'message_start','message':{'role':'assistant'}})
    emit({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':content}]}})
    emit({'type':'agent_settled'})

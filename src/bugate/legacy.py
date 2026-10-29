"""Structured reuse of legacy GateReport check functions in an isolated process."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
from .errors import BugateError
from .workspace import within

def legacy_root():
    packaged=Path(__file__).parent/'resources'
    return packaged if (packaged/'scripts/bugate_core.py').is_file() else Path(__file__).resolve().parents[2]

def check_precode(task_root,directory):
    root=legacy_root();target=within(task_root,directory)
    if not target.is_dir():raise BugateError('REFERENCE_MISSING','Legacy pre-code directory does not exist')
    for p in target.rglob('*'):
        within(task_root,p)
    code="import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from check_bugate_v13_semantics import check;r=check(Path(sys.argv[2]),scope='pre-code',require_passed=True);print(json.dumps({'name':r.name,'failures':r.failures,'warnings':r.warnings}))"
    env={k:v for k,v in os.environ.items() if k not in ('BUGATE_PROFILE','BUGATE_PROJECT_ROOT','BUGATE_ENGINE_ROOT','PYTHONPATH')}
    env.update({'BUGATE_PROJECT_ROOT':str(root),'BUGATE_ENGINE_ROOT':str(root),'BUGATE_PROFILE':''})
    try:proc=subprocess.run([sys.executable,'-c',code,str(root/'scripts'),str(target)],cwd=task_root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    except subprocess.TimeoutExpired as exc:raise BugateError('PYTHON_TIMEOUT','Legacy structured checker timed out',exit_code=124) from exc
    if len(proc.stdout)>1048576 or len(proc.stderr)>1048576:raise BugateError('OUTPUT_LIMIT','Legacy checker output exceeded limit',exit_code=1)
    if proc.returncode:raise BugateError('LEGACY_CHECKER_FAILURE','Legacy structured checker failed',exit_code=1,details=proc.stderr.decode('utf-8','replace')[:2000])
    try:return json.loads(proc.stdout)
    except ValueError as exc:raise BugateError('PYTHON_INVALID_RESPONSE','Legacy checker returned invalid JSON',exit_code=1) from exc

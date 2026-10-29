#!/usr/bin/env python3
"""Real explicit prior v2 archive -> candidate upgrade and exact archive rollback.

Archives are explicit inputs; no history is downloaded or migrated implicitly.
Only --out-dir is mutated. Old task bindings must fail under the new protocol.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous-package', type=Path, required=True)
    parser.add_argument('--previous-sha256', required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--dsh', required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    old, new = args.previous_package.resolve(), args.package.resolve()
    assert hashlib.sha256(old.read_bytes()).hexdigest() == args.previous_sha256
    def archive_versions(path):
        with tarfile.open(path, 'r:gz') as archive:
            return (json.load(archive.extractfile('package/package.json'))['version'],
                    json.load(archive.extractfile('package/resources/compatibility.json'))['core_version'])
    old_version, old_core = archive_versions(old)
    new_version, new_core = archive_versions(new)
    assert old_version != new_version
    out = args.out_dir.resolve(); out.mkdir(parents=True)
    project = out / 'project'; project.mkdir()
    home = out / 'dsh-home'; profile = 'published-v2-upgrade'
    env = os.environ.copy()
    for name in ('PYTHONPATH', 'PYTHONHOME', 'NODE_PATH'):
        env.pop(name, None)
    env['DSH_HOME'] = str(home)
    records = []
    def run(name, argv, expected=0):
        start = time.monotonic()
        result = subprocess.run(argv, cwd=out, env=env, text=True, capture_output=True, timeout=600)
        (out / (name + '.stdout')).write_text(result.stdout)
        (out / (name + '.stderr')).write_text(result.stderr)
        records.append({'name': name, 'argv': list(map(str, argv)), 'exit_code': result.returncode,
                        'elapsed_seconds': round(time.monotonic() - start, 2)})
        (out / 'commands.json').write_text(json.dumps(records, indent=2))
        assert result.returncode == expected, (name, result.returncode, result.stdout[-5000:], result.stderr[-5000:])
        print(name + ' PASS', flush=True)
        return json.loads(result.stdout)
    def install(name, archive, venv):
        return run(name, [sys.executable, str(archive.parent / 'install_dsh.py'), '--package', str(archive),
                         '--project', str(project), '--dsh-profile', profile, '--dsh', args.dsh,
                         '--dsh-home', str(home), '--venv', str(venv)])
    first = install('01-previous-archive-install', old, out / 'previous-bootstrap')
    assert first['data']['status'] == 'installed'
    assert first['data']['integration']['package_version'] == old_version
    old_python = first['data']['integration']['python']
    requirement = project / 'requirements.md'; requirement.write_text('R1: Return the input unchanged.\n')
    sut_profile = {'apiVersion':'bugate.io/v2','kind':'Profile','id':'upgrade-profile','version':'1',
                   'sources':[{'id':'SRC1','path':'requirements.md','digest':'sha256:'+hashlib.sha256(requirement.read_bytes()).hexdigest(),'description':'Temporary neutral requirement'}],
                   'requirements':['R1'],'risks':[],'goal':'Verify identity','delivery_criteria':['business_understanding'],
                   'resource_boundaries':{'network':False,'destructive':False,'notes':'Generated local fixture'},'unknowns':[]}
    profile_file = project / 'profile.json'; profile_file.write_text(json.dumps(sut_profile))
    old_task = run('02-old-task-init', [old_python,'-m','bugate','init','--project',str(project),'--task','old-task','--sut-profile',str(profile_file),'--json'])['data']['task_root']
    lock = Path(old_task) / '.bugate/protocol.lock.json'; lock_before = lock.read_bytes()
    sentinel = b'\n# user-owned upgrade sentinel\n'
    patch = home / 'profiles' / profile / 'cordis.patch.yml'; patch.write_bytes(patch.read_bytes()+sentinel)
    upgraded = install('03-actual-candidate-upgrade', new, out / 'candidate-bootstrap')
    assert upgraded['data']['status'] == 'upgraded'
    record = upgraded['data']['integration']
    assert record['package_version'] == new_version
    assert record['previous']['package_version'] == old_version
    new_python = record['python']
    mismatch = run('04-old-binding-rejected-no-migration', [new_python,'-m','bugate','protocol','prepare','--task',old_task,'--json'],2)
    assert mismatch['errors'][0]['code'] == 'PROTOCOL_MISMATCH', mismatch
    assert lock.read_bytes() == lock_before and sentinel in patch.read_bytes()
    new_task = run('05-new-v2-task-init', [new_python,'-m','bugate','init','--project',str(project),'--task','new-task','--sut-profile',str(profile_file),'--json'])['data']['task_root']
    prepared = run('06-new-v2-task-prepare', [new_python,'-m','bugate','protocol','prepare','--task',new_task,'--json'])
    assert prepared['data']['kind'] == 'PreparedProtocolContext'
    common = ['--project',str(project),'--dsh-profile',profile,'--dsh',args.dsh,'--dsh-home',str(home),'--json']
    rollback = run('07-actual-archive-rollback', [new_python,'-m','bugate','remove','deepseek-harness',*common,'--rollback'])
    assert rollback['data']['status'] == 'rolled_back'
    restored = rollback['data']['integration']
    assert restored['package_version'] == old_version and restored['core_version'] == old_core
    assert sentinel in patch.read_bytes() and lock.read_bytes() == lock_before
    run('08-restored-old-binding-prepare', [restored['python'],'-m','bugate','protocol','prepare','--task',old_task,'--json'])
    run('09-remove-preserves-both-tasks', [new_python,'-m','bugate','remove','deepseek-harness',*common])
    assert sentinel in patch.read_bytes() and lock.read_bytes() == lock_before and (Path(new_task)/'task.json').exists()
    result = {'status':'PASS','verification':'published-v2-archive-upgrade-and-rollback; no task migration',
              'previous_version':old_version,'candidate_version':new_version,'previous_core':old_core,'candidate_core':new_core,
              'previous_sha256':args.previous_sha256,'candidate_sha256':hashlib.sha256(new.read_bytes()).hexdigest(),
              'tests':len(records),'mapping':{'HC-01':['03-actual-candidate-upgrade'],'HC-04':['04-old-binding-rejected-no-migration'],
              'HC-10':['04-old-binding-rejected-no-migration'],'HC-14':['07-actual-archive-rollback','09-remove-preserves-both-tasks'],
              'AC-09':['04-old-binding-rejected-no-migration'],'AC-31':['03-actual-candidate-upgrade','07-actual-archive-rollback']}}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()

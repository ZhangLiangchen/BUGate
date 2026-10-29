#!/usr/bin/env python3
"""Real DSH CLI + clean standalone bundle installation acceptance.

This proves packaging/configuration lifecycle, not Agent prompt hydration. The
native runtime acceptance separately proves actual plugin/tool/lifecycle loads.
All projects, profiles and the upgrade package are generated in --out-dir.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,required=True);parser.add_argument('--dsh',required=True);parser.add_argument('--out-dir',type=Path,required=True);parser.add_argument('--deny-source-root',type=Path)
    args=parser.parse_args();out=args.out_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    project=out/'project';project.mkdir();home=out/'dsh-home';bootstrap=out/'bootstrap';profile='bugate-package-acceptance'
    package=args.package.resolve();install=package.parent/'install_dsh.py'
    env=os.environ.copy();env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None);env['DSH_HOME']=str(home)
    records=[]
    denied = args.deny_source_root.resolve() if args.deny_source_root else None
    if denied and sys.platform != 'darwin':
        parser.error('--deny-source-root requires the macOS sandbox; no simulated denial is accepted')
    prefix = ['/usr/bin/sandbox-exec', '-p', f'(version 1) (allow default) (deny file-read* (subpath {json.dumps(str(denied))}))'] if denied else []
    def run(name,command,expected=0):
        start=time.monotonic();p=subprocess.run(prefix+command,cwd=out,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600)
        (out/f'{name}.stdout').write_text(p.stdout);(out/f'{name}.stderr').write_text(p.stderr)
        item={'name':name,'command':prefix+command,'returncode':p.returncode,'seconds':round(time.monotonic()-start,2),'stdout':f'{name}.stdout','stderr':f'{name}.stderr'};records.append(item)
        (out/'commands.json').write_text(json.dumps(records,indent=2))
        assert p.returncode==expected,(name,p.returncode,p.stdout[-3000:],p.stderr[-3000:])
        print(name,'PASS',flush=True)
        return json.loads(p.stdout) if p.stdout.strip().startswith('{') else p.stdout
    if denied:
        probe = run('00-source-denial-probe', [sys.executable, '-c', "from pathlib import Path; import sys; p=Path(sys.argv[1])/'pyproject.toml';\ntry: p.read_text()\nexcept PermissionError: print('source access denied'); sys.exit(0)\nraise SystemExit('source denial was not enforced')", str(denied)])
        assert probe.strip() == 'source access denied'
    bootstrap_command=[sys.executable,str(install),'--package',str(package),'--project',str(project),'--dsh-profile',profile,'--dsh',args.dsh,'--dsh-home',str(home),'--venv',str(bootstrap)]
    first=run('01-clean-bootstrap',bootstrap_command)
    assert first['data']['status']=='installed'
    python=str(bootstrap/'bin/python');cli=[python,'-m','bugate'];common=['--project',str(project),'--dsh-profile',profile,'--dsh',args.dsh,'--dsh-home',str(home),'--json']
    patch=home/'profiles'/profile/'cordis.patch.yml';manifest=home/'profiles'/profile/'package.json'
    user=b'\n# USER-OWNED SENTINEL retained through upgrades and removal\n'
    patch.write_bytes(patch.read_bytes()+user)
    document=json.loads(manifest.read_text());document['user_owned_setting']={'keep':True};manifest.write_text(json.dumps(document,indent=2)+'\n')
    facts=project/'test-task'/'same-id'/'user-fact.txt';facts.parent.mkdir(parents=True);facts.write_text('must remain identical\n')
    before=patch.read_bytes()
    repeat=run('02-repeat',cli+['setup','deepseek-harness',*common,'--package',str(package)])
    assert repeat['data']['status']=='unchanged' and patch.read_bytes()==before
    doctor=run('03-doctor',cli+['doctor','integrations',*common]);assert doctor['data']['hydration']=='unobserved' and doctor['data']['projection']=='current'
    changed=run('04-task-rebind-rejected',cli+['setup','deepseek-harness',*common,'--package',str(package),'--task-path',str(facts.parent)],2)
    assert changed['errors'][0]['code']=='BINDING_MISMATCH'
    installed=home/'profiles'/profile/'node_modules'/'@bugate'/'deepseek-harness'/'lib/index.js'
    original=installed.read_bytes();installed.write_bytes(original+b'\n// injected stale projection\n')
    stale=run('05-stale-projection',cli+['doctor','integrations',*common],1);assert stale['errors'][0]['code']=='PROJECTION_STALE';installed.write_bytes(original)
    # Controlled upgrade fixture changes only release metadata, not quality rules.
    with tarfile.open(package,'r:gz') as archive:
        members={m.name:archive.extractfile(m).read() for m in archive if m.isfile()}
    metadata=json.loads(members['package/package.json']);original_version=metadata['version'];upgrade_version=original_version+'.upgrade-fixture';metadata['version']=upgrade_version;members['package/package.json']=(json.dumps(metadata,indent=2)+'\n').encode()
    inventory=json.loads(members['package/inventory.json']);inventory['files']['package.json']=hashlib.sha256(members['package/package.json']).hexdigest();members['package/inventory.json']=(json.dumps(inventory,indent=2)+'\n').encode()
    upgrade=out/'upgrade-fixture.tgz'
    with tarfile.open(upgrade,'w:gz') as archive:
        for name,payload in sorted(members.items()):
            info=tarfile.TarInfo(name);info.size=len(payload);info.mode=0o644;archive.addfile(info,io.BytesIO(payload))
    upgraded=run('06-upgrade',cli+['setup','deepseek-harness',*common,'--package',str(upgrade)]);assert upgraded['data']['status']=='upgraded'
    assert upgraded['data']['integration']['package_version']==upgrade_version
    rolled=run('07-rollback',cli+['remove','deepseek-harness',*common,'--rollback']);assert rolled['data']['integration']['package_version']==original_version and rolled['data']['integration']['previous'] is None
    run('08-doctor-after-rollback',cli+['doctor','integrations',*common])
    assert user in patch.read_bytes() and json.loads(manifest.read_text())['user_owned_setting']=={'keep':True}
    run('09-remove',cli+['remove','deepseek-harness',*common]);assert b'BEGIN BUGATE' not in patch.read_bytes()
    assert user in patch.read_bytes() and json.loads(manifest.read_text())['user_owned_setting']=={'keep':True} and facts.read_text()=='must remain identical\n'
    assert '@bugate/deepseek-harness' not in json.loads(manifest.read_text()).get('dependencies', {})
    absent=run('10-repeat-remove',cli+['remove','deepseek-harness',*common]);assert absent['data']['status']=='absent'
    # Crash after real package-manager installation but before owned patch write.
    fault_code="""from bugate import integrations as i
from pathlib import Path
import sys
real=i._atomic
def fail(path,data):
 if path.name=='cordis.patch.yml': raise OSError('injected patch disk failure')
 return real(path,data)
i._atomic=fail
try:i.setup(sys.argv[1],sys.argv[2],package=sys.argv[3],dsh=sys.argv[4],dsh_home=sys.argv[5])
except OSError as e: print(str(e));sys.exit(71)
"""
    run('11-real-host-add-patch-failure',[python,'-c',fault_code,str(project),profile,str(package),args.dsh,str(home)],71)
    state=project/'.bugate/integrations/deepseek-harness'/profile
    assert (state/'pending.json').is_file() and not (state/'installed.json').exists()
    pending=run('12-pending-no-blind-retry',cli+['setup','deepseek-harness',*common,'--package',str(package)],2);assert pending['errors'][0]['code']=='PENDING_RECOVERY_REQUIRED'
    recovered=run('13-explicit-pending-rollback',cli+['remove','deepseek-harness',*common,'--rollback']);assert recovered['data']['status']=='recovered' and not (state/'pending.json').exists()
    assert '@bugate/deepseek-harness' not in json.loads(manifest.read_text()).get('dependencies', {}) and user in patch.read_bytes() and facts.read_text()=='must remain identical\n'
    run('14-reinstall-after-recovery',cli+['setup','deepseek-harness',*common,'--package',str(package)])
    run('15-final-doctor',cli+['doctor','integrations',*common])
    unowned=out/'unowned-venv';unowned.mkdir();(unowned/'sentinel').write_text('preserve')
    denied_bootstrap_command=bootstrap_command[:-1]+[str(unowned)]
    run('16-unowned-bootstrap-rejected',denied_bootstrap_command,2);assert (unowned/'sentinel').read_text()=='preserve'
    # Crash after publishing installed.json while the recovery journal remains.
    commit_fault="""from bugate import integrations as i
import sys
real=i._write
def fail(path,data):
 real(path,data)
 if path.name=='installed.json': raise OSError('injected post-record crash')
i._write=fail
try:i.setup(sys.argv[1],sys.argv[2],package=sys.argv[3],dsh=sys.argv[4],dsh_home=sys.argv[5])
except OSError as e: print(str(e));sys.exit(72)
"""
    run('17-real-upgrade-record-failure',[python,'-c',commit_fault,str(project),profile,str(upgrade),args.dsh,str(home)],72)
    assert (state/'pending.json').is_file() and (state/'installed.json').is_file()
    run('18-post-record-recovery',cli+['remove','deepseek-harness',*common,'--rollback'])
    assert json.loads((state/'installed.json').read_text())['package_version']==original_version
    run('19-post-record-doctor',cli+['doctor','integrations',*common])
    result={'status':'PASS','level':'real DSH CLI + standalone wheel/bundle; runtime hydration not asserted','package_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
            'dsh_version':'0.2.2-alpha.2','source_denied':str(denied) if denied else None,'tests':len(records),'mapping':{'HC-01':['02-repeat'],'HC-02':['03-doctor'],'HC-11':['05-stale-projection'],'HC-14':['07-rollback','09-remove','13-explicit-pending-rollback','18-post-record-recovery'],'AC-19':['01-clean-bootstrap'],'AC-20':['01-clean-bootstrap','03-doctor'],'AC-31':['02-repeat','06-upgrade','07-rollback','09-remove','14-reinstall-after-recovery']}}
    (out/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))


if __name__=='__main__':main()

"""Run supplied-matte native QA in fully isolated Blender user directories."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--fixture-report',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
p.add_argument('--blender',default=r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe');a=p.parse_args()
out=a.output.resolve();assert out.is_relative_to((ROOT/'test-output').resolve());out.mkdir(parents=True,exist_ok=True)
env=dict(os.environ)
for key,part in [('BLENDER_USER_SCRIPTS','scripts'),('BLENDER_USER_CONFIG','config'),('BLENDER_USER_DATAFILES','datafiles'),('BLENDER_USER_EXTENSIONS','extensions')]:
    folder=out/'user'/part;folder.mkdir(parents=True,exist_ok=True);env[key]=str(folder)
reports=[]
for stage,script,extra,report in [
    ('native','blender_custom_matte_smoke.py',['--fixture-report',str(a.fixture_report.resolve())],'blender_custom_matte_report.json'),
    ('reopen','blender_custom_matte_reopen.py',['--blend',str(out/'custom_matte_native.blend')],'blender_custom_matte_reopen_report.json')]:
    command=[a.blender,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(ROOT/'tests'/script),'--',*extra,'--output',str(out)]
    (out/(stage+'_invocation.json')).write_text(json.dumps({'command':command,'isolation':{k:v for k,v in env.items() if k.startswith('BLENDER_USER_')}},indent=2))
    with (out/(stage+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    data=json.loads((out/report).read_text()) if (out/report).is_file() else {'status':'missing_report'}
    reports.append({'stage':stage,'exit_code':result.returncode,'status':data['status'],'checks':len(data.get('checks',[]))})
    print(json.dumps(reports[-1]),flush=True)
    if result.returncode or data['status']!='passed':raise SystemExit(1)
(out/'native_summary.json').write_text(json.dumps({'status':'passed','stages':reports},indent=2))

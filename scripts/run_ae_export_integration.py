"""Exercise the packaged CLI and external exporter on an existing real depth run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--manifest', required=True)
parser.add_argument('--runtime-config', default=str(Path.home()/'.codex/runtime/blender-depth-reference/runtime.json'))
parser.add_argument('--output', required=True)
args = parser.parse_args()
out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=True)
release = json.loads((ROOT/'dist/release.json').read_text())
archive = Path(release['addon']['path'])
assert hashlib.sha256(archive.read_bytes()).hexdigest() == release['addon']['sha256']
extracted = out/'installed'
assert not extracted.exists(), 'Use a fresh integration output folder'
with zipfile.ZipFile(archive) as z:
    for name in z.namelist():
        target = (extracted/name).resolve()
        assert target.is_relative_to(extracted) and name.startswith('local_depth_reference/')
    z.extractall(extracted)
original_config = json.loads(Path(args.runtime_config).read_text(encoding='utf-8-sig'))
# Export must not require checkpoints, inference repos, matting, or AE itself.
config = out/'export-runtime-only.json'
config.write_text(json.dumps({'runtime': {'vda_python': original_config['runtime']['vda_python']}}, indent=2))
manifest = Path(args.manifest).resolve()
parent = json.loads(manifest.read_text())
shot_manifest = Path(parent['binding']['shot_dir'])/'shot.json'
originals = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (manifest, shot_manifest) if p.exists()}
cli = extracted/'local_depth_reference/cli.py'
command = [sys.executable, str(cli), 'export-ae', '--manifest', str(manifest), '--coverage', 'both', '--config', str(config), '--wait']
(out/'invocation.json').write_text(json.dumps({'command':command,'zip_sha256':release['addon']['sha256']}, indent=2))
with (out/'export.log').open('w', encoding='utf-8') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
text = (out/'export.log').read_text(encoding='utf-8')
assert result.returncode == 0, text
state = json.loads(text)
assert state['status']=='complete' and state['job_kind']=='ae_export', state
check = subprocess.run([sys.executable,str(cli),'validate','--manifest',state['manifest_path']], capture_output=True,text=True)
assert check.returncode == 0, check.stdout+check.stderr
validated=json.loads(check.stdout)
assert validated['coverage']=='both' and validated['frame_count']==parent['source']['frame_count']
assert set(validated['streams'])=={'whole_scene','person','person_alpha'}
assert (validated['fps_num'],validated['fps_den'])==(parent['source']['fps_num'],parent['source']['fps_den'])
assert Path(validated['run_dir']).parent == Path(parent['binding']['depth_root'])
assert Path(validated['run_dir']) != manifest.parent
for path,sha in originals.items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha, 'Input changed: '+path
report = {'status':'passed','version':release['version'],'zip_sha256':release['addon']['sha256'],
          'manifest':state['manifest_path'],'source_manifest':str(manifest),
          'source_and_shot_manifests_unchanged':True,'minimal_export_runtime':True,
          'coverage':validated['coverage'],'frames':validated['frame_count'],
          'fps_num':validated['fps_num'],'fps_den':validated['fps_den'],
          'streams':validated['streams']}
(out/'integration_report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))

"""Build and test the release in an isolated Blender user directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--blender', default=r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe')
parser.add_argument('--manifest', required=True)
parser.add_argument('--runtime-config', default=str(Path.home() / '.codex/runtime/blender-depth-reference/runtime.json'))
parser.add_argument('--output', default=str(ROOT / 'test-output/package-smoke'))
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py')], check=True)
release = json.loads((ROOT / 'dist/release.json').read_text())
env = dict(os.environ)
for key, relative in (('BLENDER_USER_SCRIPTS', 'scripts'), ('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_DATAFILES', 'datafiles'), ('BLENDER_USER_EXTENSIONS', 'extensions')):
    path = output / 'package-user' / relative
    path.mkdir(parents=True, exist_ok=True)
    env[key] = str(path)
(output / 'package-user/scripts/addons').mkdir(parents=True, exist_ok=True)
command = [args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
           '--python', str(ROOT / 'tests/blender_package_smoke.py'), '--',
           '--zip', release['addon']['path'], '--manifest', str(Path(args.manifest).resolve()),
           '--output', str(output), '--runtime-config', str(Path(args.runtime_config).resolve())]
(output / 'invocation.json').write_text(json.dumps({'command': command, 'isolated_scripts': env['BLENDER_USER_SCRIPTS'],
                                                 'zip_sha256': release['addon']['sha256']}, indent=2))
with (output / 'blender.log').open('w', encoding='utf-8') as stream:
    result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
report_path = output / 'package_smoke_report.json'
report = json.loads(report_path.read_text()) if report_path.exists() else {'status': 'missing_report'}
print(json.dumps({'exit_code': result.returncode, 'status': report['status'], 'report': str(report_path),
                  'log': str(output / 'blender.log')}, indent=2))
raise SystemExit(0 if result.returncode == 0 and report['status'] == 'passed' else 1)

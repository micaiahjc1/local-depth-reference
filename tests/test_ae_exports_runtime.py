"""Independent AE-export orchestration tests; temporary shots and tiny fake workers only."""
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'local_depth_reference'))
import runtime
from shot_paths import DepthError, atomic_json, load_package, load_ae_package, read_json, reserve_run, resolve_shot


class AEExportRuntime(unittest.TestCase):
    def test_progress_read_recovers_transient_windows_sharing_failure(self):
        original_open = Path.open
        attempts = []
        def transient(path, *args, **kwargs):
            attempts.append(path)
            if len(attempts) == 1:
                raise PermissionError('transient Windows replacement sharing violation')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', transient):
            self.assertEqual(read_json(self.manifest)['type'], 'local-depth-reference')
        self.assertEqual(len(attempts), 2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ldr-ae-export-tests-')
        self.root = Path(self.temporary.name)
        self.shot = self.root / 'Shot with spaces'
        (self.shot / 'Resources').mkdir(parents=True)
        self.identity = {'schema': 'blenderae.shot', 'schema_version': 1,
                         'project_id': '13fb171d-de31-4151-b090-f681bc7a72a0',
                         'shot_id': '3d383f8e-48ec-4bad-ad76-7580efe8abeb', 'revision': 4}
        atomic_json(self.shot / 'shot.json', self.identity)
        self.binding = resolve_shot(self.shot)
        self.parent = reserve_run(self.binding)
        paths, inventory = {}, []
        for name in ('raw_depth', 'clean_depth', 'alpha', 'source'):
            suffix = '.exr' if 'depth' in name else '.png'
            directory = self.parent / name
            directory.mkdir()
            for index in range(3):
                path = directory / f'{index:05d}{suffix}'
                payload = f'fixture {name} {index}'.encode()
                path.write_bytes(payload)
                inventory.append({'path': path.relative_to(self.parent).as_posix(), 'bytes': len(payload),
                                  'sha256': hashlib.sha256(payload).hexdigest()})
            paths[name] = f'{name}/00000{suffix}'
        self.manifest = self.parent / 'manifest.json'
        self.package = {'schema': 1, 'type': 'local-depth-reference', 'status': 'complete',
                        'source': {'path': str(self.root / 'removed original.mp4'), 'sha256': 'a'*64,
                                   'width': 64, 'height': 36, 'frame_count': 3, 'fps_num': 24000, 'fps_den': 1001},
                        'processing': {'working_width': 32, 'working_height': 18, 'q_median': 1.0},
                        'sequence': {'first_index': 0, 'count': 3, 'digits': 5}, 'paths': paths,
                        'binding': self.binding, 'stage_cache': {'fixture': {'artifacts': inventory}}}
        atomic_json(self.manifest, self.package)
        self.config = self.root / 'runtime.json'
        # No inference repository, model, SAM, matting Python, or original video is present.
        atomic_json(self.config, {'runtime': {'vda_python': sys.executable}})
        self.fake_worker = self.root / 'ae_export.py'
        self.fake_worker.write_text('''import argparse,json,pathlib,time,sys
p=argparse.ArgumentParser();p.add_argument('--job');a=p.parse_args()
j=json.loads(pathlib.Path(a.job).read_text());r=pathlib.Path(j['run_dir'])
def write(name,data):
 t=r/(name+'.tmp');t.write_text(json.dumps(data));t.replace(r/name)
def status(value):write('progress.json',{'status':value,'job_kind':'ae_export'})
status('running')
for _ in range(40):
 if (r/'cancel.flag').exists():status('cancelled');sys.exit(21)
 time.sleep(.015)
if j['settings'].get('fixture_fail'):
 status('error');sys.exit(1)
write('manifest.json',{'schema':1,'type':'local-depth-ae-export','status':'complete'})
status('complete')
''', encoding='utf-8')
        atomic_json(self.config, {'runtime': {'vda_python': sys.executable}, 'ae_export_worker': str(self.fake_worker)})

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, **kwargs):
        return runtime.create_ae_export(self.manifest, config_path=str(self.config), **kwargs)

    def substitute_fake_worker(self, job):
        job = dict(job)
        job['worker'] = str(self.fake_worker)
        atomic_json(Path(job['run_dir']) / 'job.json', job)
        return job

    def snapshot_parent(self):
        return {str(path.relative_to(self.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.parent.rglob('*') if path.is_file()}

    def ae_package(self):
        run=reserve_run(self.binding)
        def chunk(kind,payload):
            return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
        inventory=[]
        for directory in ('ae/whole_scene','ae/person','ae/alpha'):
            folder=run/directory;folder.mkdir(parents=True)
            for index in range(3):
                pixels=b''.join(b'\0'+struct.pack('>H',32768+index*1000)*32 for _ in range(18))
                data=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',32,18,16,0,0,0,0))+
                      chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))
                path=folder/f'{index:05d}.png';path.write_bytes(data)
                inventory.append({'path':path.relative_to(run).as_posix(),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        data={'schema':1,'type':'local-depth-ae-export','status':'complete','coverage':'both',
              'source':self.package['source'],'processing':self.package['processing'],'sequence':self.package['sequence'],
              'binding':self.binding,'encoding':{'dtype':'uint16','person_depth_premultiplied':False},
              'streams':{'whole_scene':{'path':'ae/whole_scene/00000.png','near':.5,'far':25},
                         'person':{'path':'ae/person/00000.png','alpha_path':'ae/alpha/00000.png','near':.5,'far':3}},
              'inventory':inventory}
        path=run/'manifest.json';atomic_json(path,data)
        return path,data

    def test_export_needs_no_inference_assets_or_original_video(self):
        loaded = runtime.load_config(str(self.config), for_export=True)
        self.assertEqual(loaded['runtime']['vda_python'], sys.executable)
        self.assertEqual(Path(loaded['worker']).name, 'ae_export.py')
        job = self.create()
        self.assertEqual(job['kind'], 'ae_export')
        self.assertEqual(job['settings']['ae_coverage'], 'both')
        self.assertEqual(Path(job['source_manifest']), self.manifest)

    def test_new_version_same_binding_and_parent_immutable(self):
        before = self.snapshot_parent()
        shot_before = (self.shot / 'shot.json').read_bytes()
        job = self.create()
        self.assertEqual(Path(job['run_dir']).parent, self.parent.parent)
        self.assertEqual(Path(job['run_dir']).name, 'v002')
        self.assertIn('Resources', Path(job['run_dir']).parts)
        self.assertEqual(job['binding']['shot_id'], self.identity['shot_id'])
        self.assertEqual(job['source_manifest_sha256'], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual(self.snapshot_parent(), before)
        self.assertEqual((self.shot / 'shot.json').read_bytes(), shot_before)

    def test_malformed_shot_paths_fail_without_reserving_a_version(self):
        before = set(self.parent.parent.iterdir())
        for field in ('resources_dir', 'depth_root'):
            for value in (None, [], 3, ''):
                data = {**self.package, 'binding': {**self.binding, field: value}}
                atomic_json(self.manifest, data)
                with self.subTest(field=field, value=value), self.assertRaises(DepthError):
                    self.create()
        self.assertEqual(set(self.parent.parent.iterdir()), before)

    def test_all_coverage_choices_and_invalid_coverage_no_reservation(self):
        for coverage in ('whole_scene', 'person', 'both'):
            self.assertEqual(self.create(coverage=coverage)['settings']['ae_coverage'], coverage)
        before = set(self.parent.parent.iterdir())
        for invalid in ('', 'whole', 'PERSON', 'background', None):
            with self.subTest(coverage=invalid), self.assertRaises((DepthError, ValueError)):
                self.create(coverage=invalid)
        self.assertEqual(set(self.parent.parent.iterdir()), before)

    def test_noncompleted_and_wrong_manifest_types_rejected(self):
        for field, value in [('status', 'running'), ('type', 'local-depth-ae-export'), ('type', 'local-depth-export-comparison')]:
            data = dict(self.package); data[field] = value
            atomic_json(self.manifest, data)
            with self.subTest(field=field, value=value), self.assertRaises((DepthError, ValueError)):
                self.create()
        self.assertEqual(list(self.parent.parent.iterdir()), [self.parent])

    def test_stale_shot_identity_rejected_before_reservation(self):
        atomic_json(self.shot / 'shot.json', {**self.identity, 'shot_id': '00000000-0000-4000-8000-000000000001'})
        with self.assertRaises((DepthError, ValueError)):
            self.create()
        self.assertEqual(list(self.parent.parent.iterdir()), [self.parent])

    def test_complete_ae_manifest_is_never_a_blender_import(self):
        atomic_json(self.manifest, {**self.package, 'type': 'local-depth-ae-export'})
        with self.assertRaises(DepthError):
            load_package(self.manifest)

    def test_cancel_resume_and_progress_routing_do_not_touch_parent(self):
        before = self.snapshot_parent()
        job = self.substitute_fake_worker(self.create(coverage='person'))
        run = Path(job['run_dir'])
        runtime.launch_job(run / 'job.json')
        runtime.request_cancel(run)
        cancelled = runtime.wait_job(run)
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertFalse((run / 'manifest.json').exists())
        runtime.resume_job(run)
        completed = runtime.wait_job(run)
        self.assertEqual(completed['status'], 'complete')
        self.assertEqual(completed['job_kind'], 'ae_export')
        self.assertEqual(Path(completed['ae_manifest_path']), run / 'manifest.json')
        self.assertFalse((run / 'cancel.flag').exists())
        self.assertEqual(self.snapshot_parent(), before)

    def test_failed_export_not_importable_and_retry_stays_in_owned_version(self):
        job = self.substitute_fake_worker(self.create())
        job['settings']['fixture_fail'] = True
        run = Path(job['run_dir']); atomic_json(run / 'job.json', job)
        runtime.launch_job(run / 'job.json')
        self.assertEqual(runtime.wait_job(run)['status'], 'error')
        self.assertFalse((run / 'manifest.json').exists())
        job['settings']['fixture_fail'] = False
        atomic_json(run / 'job.json', job)
        runtime.resume_job(run)
        self.assertEqual(runtime.wait_job(run)['status'], 'complete')
        self.assertEqual(len(list(self.parent.parent.iterdir())), 2)
        for action in (lambda: runtime.resume_job(run), lambda: runtime.request_cancel(run), lambda: runtime.launch_job(run / 'job.json')):
            with self.assertRaises(DepthError): action()

    def test_completed_base_package_remains_usable(self):
        before = load_package(self.manifest)
        self.create()
        after = load_package(self.manifest)
        self.assertEqual(before, after)

    def test_ae_portable_package_validation_and_cli_dispatch(self):
        path,data=self.ae_package()
        validated=load_ae_package(path)
        self.assertEqual(set(validated['streams']),{'whole_scene','person','person_alpha'})
        self.assertEqual(validated['frame_count'],3)
        result=subprocess.run([sys.executable,str(ROOT/'local_depth_reference/cli.py'),'validate','--manifest',str(path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['coverage'],'both')
        with self.assertRaises(DepthError):load_package(path)

    def test_ae_package_rejects_corrupt_middle_frame(self):
        path,_=self.ae_package()
        member=path.parent/'ae/person/00001.png'
        member.write_bytes(member.read_bytes()+b'changed')
        with self.assertRaisesRegex(DepthError,'integrity'):load_ae_package(path)

    def test_ae_package_rejects_semantic_and_path_mismatch(self):
        path,data=self.ae_package()
        mutations=[lambda x:x['encoding'].update(person_depth_premultiplied=True),
                   lambda x:x['streams']['person'].update(far=.1),
                   lambda x:x.update(coverage='whole_scene'),
                   lambda x:x['streams']['person'].update(alpha_path='../alpha/00000.png'),
                   lambda x:x['inventory'].append(dict(x['inventory'][0]))]
        for mutation in mutations:
            changed=json.loads(json.dumps(data));mutation(changed);atomic_json(path,changed)
            with self.assertRaises(DepthError):load_ae_package(path)

    def test_ae_resume_cannot_change_the_selected_person(self):
        job=self.substitute_fake_worker(self.create())
        run=Path(job['run_dir']);before=(run/'job.json').read_bytes()
        for kwargs in ({'subject_id':2},{'subject_points':[]}):
            with self.assertRaisesRegex(DepthError,'selected person'):runtime.resume_job(run,**kwargs)
        self.assertEqual((run/'job.json').read_bytes(),before)

    def test_cli_export_wait_uses_separate_job_and_default_both(self):
        result=subprocess.run([sys.executable,str(ROOT/'local_depth_reference/cli.py'),'export-ae',
                               '--manifest',str(self.manifest),'--config',str(self.config),'--wait'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        state=json.loads(result.stdout)
        self.assertEqual(state['job_kind'],'ae_export')
        self.assertEqual(state['status'],'complete')
        job=read_json(Path(state['run_dir'])/'job.json')
        self.assertEqual(job['settings']['ae_coverage'],'both')
        self.assertEqual(Path(job['source_manifest']),self.manifest)

    def test_cli_has_separate_export_action_and_rejects_invalid_input(self):
        cli = ROOT / 'local_depth_reference/cli.py'
        help_result = subprocess.run([sys.executable, str(cli), 'export-ae', '--help'], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('--manifest', help_result.stdout)
        self.assertIn('whole_scene', help_result.stdout)
        result = subprocess.run([sys.executable, str(cli), 'export-ae', '--manifest', str(self.manifest),
                                 '--coverage', 'invalid', '--config', str(self.config)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.parent.parent.iterdir()), [self.parent])


if __name__ == '__main__':
    unittest.main(verbosity=2)

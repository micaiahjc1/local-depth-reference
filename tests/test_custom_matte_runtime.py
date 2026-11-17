"""Custom-matte job configuration and CLI routing, without Blender or model imports."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'local_depth_reference'))
import cli
import runtime
from shot_paths import DepthError, atomic_json, read_json


class CustomMatteRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ldr-custom-runtime-')
        self.root = Path(self.temp.name)
        self.shot = self.root/'Shot'
        self.shot.mkdir()
        (self.shot/'Resources').mkdir()
        self.source = self.root/'source.mp4'
        self.source.write_bytes(b'fixture')
        self.matte = self.root/'artist matte 01001.PNG'
        self.matte.write_bytes(b'worker validates contents outside Blender')
        self.config = self.root/'runtime.json'
        self.cfg = {'runtime': {'vda_python':sys.executable, 'vda_repo':str(self.root), 'vda_checkpoint':str(self.source)}}
        atomic_json(self.config, self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, **settings):
        return runtime.create_job(self.source, self.shot, config_path=self.config,
                                  settings={'matte_source':'custom', 'matte_path':str(self.matte), **settings})

    def test_custom_runs_without_automatic_models(self):
        job = self.job(matte_channel='alpha', matte_invert=True)
        settings = read_json(job['job_path'])['settings']
        self.assertEqual(settings['matte_source'], 'custom')
        self.assertEqual(settings['matte_path'], str(self.matte.resolve()))
        self.assertEqual(settings['matte_channel'], 'alpha')
        self.assertTrue(settings['matte_invert'])
        self.assertNotIn('matting_python', job['runtime'])
        self.assertEqual(Path(job['run_dir']).parent, self.shot/'Resources'/'depth map')

    def test_automatic_still_requires_matting_models(self):
        with self.assertRaisesRegex(DepthError, 'matting_python'):
            runtime.create_job(self.source, self.shot, config_path=self.config)
        self.assertFalse((self.shot/'Resources'/'depth map').exists())

    def test_automatic_remains_default_and_ignores_old_custom_path(self):
        self.cfg['runtime'].update({k:str(self.source) for k in ('matting_python','sam3_module_root','sam3_checkpoint','matanyone_root','matanyone_checkpoint')})
        atomic_json(self.config, self.cfg)
        job = runtime.create_job(self.source, self.shot, config_path=self.config, settings={'matte_path':'offline.png'})
        self.assertEqual(job['settings']['matte_source'], 'automatic')

    def test_bad_custom_options_do_not_reserve_versions(self):
        for settings in ({'matte_path':''}, {'matte_path':str(self.root/'missing0001.png')},
                         {'matte_path':str(self.source)}, {'matte_channel':'red'},
                         {'matte_invert':'false'}, {'matte_source':'other'}):
            with self.subTest(settings=settings), self.assertRaises(DepthError):
                self.job(**settings)
        self.assertFalse((self.shot/'Resources'/'depth map').exists())

    def test_custom_configuration_check_cli(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['configure','--check','--config',str(self.config),'--matte-source','custom']),0)

    def test_cli_explicit_options_override_settings_json(self):
        settings = self.root/'settings.json'
        atomic_json(settings, {'matte_source':'automatic', 'matte_channel':'luma', 'matte_invert':True})
        with patch('runtime.launch_job', return_value={'status':'ready'}) as launch, contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(['generate','--source',str(self.source),'--shot',str(self.shot),'--config',str(self.config),
                             '--settings-json',str(settings),'--matte-source','custom','--matte-path',str(self.matte),
                             '--matte-channel','alpha','--no-matte-invert'])
        self.assertEqual(code,0)
        saved = read_json(launch.call_args.args[0])['settings']
        self.assertEqual(saved['matte_source'],'custom')
        self.assertEqual(saved['matte_channel'],'alpha')
        self.assertIs(saved['matte_invert'],False)

    def test_custom_resume_refuses_subject_selection(self):
        job = self.job()
        with patch('runtime.launch_job') as launch:
            for kwargs in ({'subject_id':2}, {'subject_points':[]}):
                with self.assertRaisesRegex(DepthError,'supplied matte'):
                    runtime.resume_job(job['run_dir'],**kwargs)
            launch.assert_not_called()
            runtime.resume_job(job['run_dir'])
            launch.assert_called_once()

    def test_old_jobs_resume_with_automatic_subject_controls(self):
        job = self.job()
        data = read_json(job['job_path'])
        for k in ('matte_source','matte_path','matte_channel','matte_invert'):
            data['settings'].pop(k,None)
        atomic_json(job['job_path'],data)
        with patch('runtime.launch_job'):
            runtime.resume_job(job['run_dir'],subject_id=2)
        self.assertEqual(read_json(job['job_path'])['settings']['subject_id'],2)


if __name__ == '__main__':
    unittest.main()

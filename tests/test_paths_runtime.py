"""Filesystem ownership, package rejection, lifecycle and CLI contracts."""
import concurrent.futures
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_depth_reference"))
import runtime
from shot_paths import DepthError, atomic_json, load_package, read_json, reserve_run, resolve_shot


class PathsAndRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ldr-tests-")
        self.root = Path(self.temp.name)
        self.shot = self.root / "Shot 02"
        self.shot.mkdir()
        self.resources = self.shot / "Resources"
        self.resources.mkdir()
        self.shot_manifest = {"schema": "blenderae.shot", "schema_version": 1,
                              "project_id": "13fb171d-de31-4151-b090-f681bc7a72a0", "shot_id": "3d383f8e-48ec-4bad-ad76-7580efe8abeb", "revision": 3}
        atomic_json(self.shot / "shot.json", self.shot_manifest)
        self.source = self.root / "source video.mp4"
        self.source.write_bytes(b"fixture")
        self.worker = self.root / "worker.py"
        self.worker.write_text('''import argparse,json,pathlib,time,sys,os
p=argparse.ArgumentParser();p.add_argument('--job');a=p.parse_args()
j=json.loads(pathlib.Path(a.job).read_text());r=pathlib.Path(j['run_dir'])
def status(value):
 t=r/'test.tmp';t.write_text(json.dumps({'status':value}));t.replace(r/'progress.json')
status('running')
for _ in range(30):
 if (r/'cancel.flag').exists():status('cancelled');sys.exit(21)
 time.sleep(.025)
if j['settings'].get('subject_id') is None:
 (r/'awaiting_subject.json').write_text(json.dumps({'candidates':[{'id':2}]}))
 status('awaiting_subject');sys.exit(20)
status('complete')
''', encoding="utf-8")
        config = {"worker": str(self.worker), "runtime": {
            key: str(self.root) for key in ("vda_repo", "vda_checkpoint", "sam3_module_root", "sam3_checkpoint", "matanyone_root", "matanyone_checkpoint")}}
        config["runtime"].update(vda_python=sys.executable, matting_python=sys.executable)
        self.config = self.root / "runtime.json"
        atomic_json(self.config, config)

    def tearDown(self):
        self.temp.cleanup()

    def job(self):
        return runtime.create_job(self.source, self.shot, config_path=self.config)

    def package(self):
        run = reserve_run(resolve_shot(self.shot))
        streams = {}
        for name in ("source", "alpha", "raw_depth", "clean_depth"):
            folder = run / name
            folder.mkdir()
            suffix = ".exr" if "depth" in name else ".png"
            for i in range(3):
                (folder / f"{i:05d}{suffix}").write_bytes(b"fixture")
            streams[name] = f"{name}/00000{suffix}"
        data = {"schema": 1, "type": "local-depth-reference", "status": "complete",
                "source": {"width": 1280, "height": 720, "frame_count": 3, "fps_num": 24000, "fps_den": 1001},
                "processing": {"working_width": 640, "working_height": 360},
                "sequence": {"first_index": 0, "count": 3, "digits": 5}, "paths": streams,
                "binding": resolve_shot(self.shot)}
        data["stage_cache"] = {"fixture": {"artifacts": [
            {"path": p.relative_to(run).as_posix(), "bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in run.rglob("*") if p.is_file()]}}
        atomic_json(run / "manifest.json", data)
        return run / "manifest.json", data

    def test_existing_case_and_no_write_on_resolve(self):
        original = (self.shot / "shot.json").read_bytes()
        result = resolve_shot(self.shot)
        self.assertEqual(Path(result["resources_dir"]), self.resources)
        self.assertEqual(Path(result["depth_root"]), self.resources / "depth map")
        self.assertFalse(Path(result["depth_root"]).exists())
        self.assertEqual((self.shot / "shot.json").read_bytes(), original)

    def test_missing_destination_defaults_lowercase(self):
        self.resources.rmdir()
        self.assertEqual(Path(resolve_shot(self.shot)["resources_dir"]).name, "resources")

    def test_link_ids_reject_wrong_shot(self):
        link = {"manifest_path": str(self.shot / "shot.json"), "shot_id": "other"}
        with self.assertRaisesRegex(DepthError, "disagrees"):
            resolve_shot(scene_binding=link)
        self.assertEqual(resolve_shot(self.shot, scene_binding=link)["shot_id"], "3d383f8e-48ec-4bad-ad76-7580efe8abeb")

    def test_native_link_and_blend_parent_resolve(self):
        blender = self.shot / "Blender"
        blender.mkdir()
        blend = blender / "Scene.blend"
        link = {"manifest_path": "//../shot.json", "shot_id": "3d383f8e-48ec-4bad-ad76-7580efe8abeb", "project_id": "13fb171d-de31-4151-b090-f681bc7a72a0"}
        self.assertEqual(resolve_shot(scene_binding=link, blend_path=str(blend))["shot_id"], "3d383f8e-48ec-4bad-ad76-7580efe8abeb")
        with self.assertRaises(DepthError): resolve_shot(blend_path=str(blend))

    def test_explicit_legacy_and_no_cwd_guess(self):
        (self.shot / "shot.json").unlink()
        self.assertEqual(resolve_shot(self.shot)["binding_kind"], "explicit_folder")
        with self.assertRaises(DepthError): resolve_shot()
        with self.assertRaises(DepthError): resolve_shot(blend_path=str(self.shot / "x.blend"))

    def test_bad_manifest_and_file_instead_of_resources(self):
        atomic_json(self.shot / "shot.json", {})
        with self.assertRaises(DepthError): resolve_shot(self.shot)
        atomic_json(self.shot / "shot.json", {"schema": "other"})
        with self.assertRaises(DepthError): resolve_shot(self.shot)
        atomic_json(self.shot / "shot.json", self.shot_manifest)
        self.resources.rmdir()
        self.resources.write_text("not folder")
        with self.assertRaises(DepthError): resolve_shot(self.shot)

    def test_atomic_version_reservations(self):
        binding = resolve_shot(self.shot)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: reserve_run(binding), range(24)))
        self.assertEqual(len(set(results)), 24)
        self.assertEqual(sorted(int(p.name[1:]) for p in results), list(range(1,25)))

    def test_binding_changed_before_write(self):
        binding = resolve_shot(self.shot)
        atomic_json(self.shot / "shot.json", {**self.shot_manifest, "shot_id": "00000000-0000-4000-8000-000000000000"})
        with self.assertRaisesRegex(DepthError, "changed"):
            reserve_run(binding)

    def test_package_success_missing_middle_and_extra(self):
        path, _ = self.package()
        self.assertEqual(load_package(path)["fps_num"], 24000)
        middle = path.parent / "alpha/00001.png"
        middle.unlink()
        with self.assertRaisesRegex(DepthError, "frame 1"): load_package(path)
        middle.write_bytes(b"fixture")
        (middle.parent / "00003.png").write_bytes(b"fixture")
        with self.assertRaisesRegex(DepthError, "extra"): load_package(path)

    def test_package_traversal_and_numeric_validation(self):
        path, data = self.package()
        for bad in ("../escape/00000.png", str(self.root / "00000.png")):
            data["paths"]["alpha"] = bad
            atomic_json(path, data)
            with self.assertRaises(DepthError): load_package(path)
        data["paths"]["alpha"] = "alpha/00000.png"
        data["source"]["fps_den"] = 0
        atomic_json(path, data)
        with self.assertRaises(DepthError): load_package(path)

    def test_package_incomplete_and_empty_rejected(self):
        path, data = self.package()
        data["status"] = "running"
        atomic_json(path, data)
        with self.assertRaises(DepthError): load_package(path)
        data["status"] = "complete"
        atomic_json(path, data)
        (path.parent / "source/00002.png").write_bytes(b"")
        with self.assertRaises(DepthError): load_package(path)

    def test_package_malformed_nested_types(self):
        path, data = self.package()
        for key in ("source", "processing", "sequence", "paths", "binding"):
            original = data[key]
            for value in (None, [], 1, "bad"):
                data[key] = value
                atomic_json(path, data)
                with self.assertRaises(DepthError): load_package(path)
            data[key] = original

    def test_subject_choice_clears_points(self):
        job = self.job()
        run = Path(job["run_dir"])
        job["settings"]["subject_points"] = [{"frame": 0, "positive": [[5, 5]], "negative": []}]
        atomic_json(run / "job.json", job)
        runtime.resume_job(run, subject_id=2)
        self.assertEqual(runtime.wait_job(run)["status"], "complete")
        self.assertEqual(read_json(run / "job.json")["settings"]["subject_points"], [])

    def test_nonempty_corrupt_frame_rejected(self):
        path, data = self.package()
        (path.parent / "source/00002.png").write_bytes(b"wrongxx")
        with self.assertRaisesRegex(DepthError, "integrity"): load_package(path)

    def test_stale_launcher_file_recovers(self):
        job = self.job()
        (Path(job["run_dir"]) / "launcher.lock").write_bytes(b"")
        runtime.launch_job(job["job_path"])
        self.assertEqual(runtime.wait_job(job["run_dir"])["status"], "awaiting_subject")

    def test_resume_replaces_previous_terminal_progress_before_launch(self):
        job = self.job()
        run = Path(job["run_dir"])
        atomic_json(run / "progress.json", {"status": "awaiting_subject"})
        runtime.resume_job(run, subject_id=2)
        self.assertEqual(runtime.read_progress(run)["status"], "running")
        self.assertEqual(runtime.wait_job(run)["status"], "complete")

    def test_bad_runtime_structure_is_actionable(self):
        atomic_json(self.config, {"runtime": []})
        with self.assertRaises(DepthError): runtime.load_config(self.config)

    def test_committed_manifest_survives_lost_final_progress_update(self):
        job = self.job()
        run = Path(job["run_dir"])
        atomic_json(run / "progress.json", {"status": "running"})
        atomic_json(run / "process.json", {"pid": 99999999})
        atomic_json(run / "manifest.json", {"schema": 1, "type": "local-depth-reference", "status": "complete"})
        self.assertEqual(runtime.read_progress(run)["status"], "complete")

    def test_lifecycle_subject_resume_and_duplicate_launch(self):
        job = self.job()
        handle = runtime.launch_job(job["job_path"])
        self.assertTrue(runtime._pid_alive(handle["pid"]))
        with self.assertRaisesRegex(DepthError, "already running"): runtime.launch_job(job["job_path"])
        state = runtime.wait_job(job["run_dir"])
        self.assertEqual(state["status"], "awaiting_subject")
        self.assertEqual(state["subject_selection"]["candidates"][0]["id"], 2)
        runtime.resume_job(job["run_dir"], subject_id=2)
        self.assertEqual(runtime.wait_job(job["run_dir"])["status"], "complete")

    def test_cancel_and_resume_stale_flag(self):
        job = self.job()
        runtime.launch_job(job["job_path"])
        runtime.request_cancel(job["run_dir"])
        self.assertEqual(runtime.wait_job(job["run_dir"])["status"], "cancelled")
        runtime.resume_job(job["run_dir"], subject_id=2)
        self.assertFalse((Path(job["run_dir"]) / "cancel.flag").exists())
        self.assertEqual(runtime.wait_job(job["run_dir"])["status"], "complete")

    def test_immutable_complete_and_job_destination_tamper(self):
        job = self.job()
        run = Path(job["run_dir"])
        atomic_json(run / "manifest.json", {"status": "complete"})
        for action in (lambda: runtime.launch_job(job["job_path"]), lambda: runtime.resume_job(run), lambda: runtime.request_cancel(run)):
            with self.assertRaises(DepthError): action()
        (run / "manifest.json").unlink()
        job["run_dir"] = str(self.root)
        atomic_json(run / "job.json", job)
        with self.assertRaises(DepthError): runtime.launch_job(run / "job.json")

    def test_cli_wait_exit_codes_and_read_only_commands(self):
        cli = ROOT / "local_depth_reference/cli.py"
        result = subprocess.run([sys.executable, str(cli), "resolve", "--shot", str(self.shot)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["shot_id"], "3d383f8e-48ec-4bad-ad76-7580efe8abeb")
        result = subprocess.run([sys.executable, str(cli), "generate", "--source", str(self.source), "--shot", str(self.shot), "--config", str(self.config), "--wait"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 20, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "awaiting_subject")


if __name__ == "__main__":
    unittest.main(verbosity=2)

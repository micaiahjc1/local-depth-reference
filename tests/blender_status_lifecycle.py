"""Native Blender lifecycle checks; isolated files and a model-free owned process."""
import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT/'test-output/status-021')
parser.add_argument('--python-executable', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {'checks': [], 'blender': bpy.app.version_string}
CHILD = None


def check(name, condition):
    REPORT['checks'].append({'name': name, 'passed': bool(condition)})
    if not condition:
        raise AssertionError(name)


def write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


def run_fixture(index, status='running', fraction=.58, kind='depth', complete=False):
    shot = OUT/'fixture-shot'
    resources = shot/'Resources'
    depth = resources/'depth map'
    run = depth/f'v{index:03d}'
    run.mkdir(parents=True, exist_ok=True)
    binding = {'binding_kind': 'explicit_folder', 'shot_dir': str(shot),
               'resources_dir': str(resources), 'depth_root': str(depth)}
    write(run/'job.json', {'schema': 1, 'kind': kind, 'run_dir': str(run), 'shot_dir': str(shot),
                          'binding': binding, 'runtime': {}, 'settings': {}})
    write(run/'process.json', {'pid': CHILD.pid})
    write(run/'progress.json', {'status': status, 'stage': 'depth', 'fraction': fraction,
                               'message': 'Fixture '+status})
    if complete:
        write(run/'manifest.json', {'schema': 1, 'status': 'complete',
                                   'type': 'local-depth-ae-export' if kind == 'ae_export' else 'local-depth-reference'})
    return run


def record(scene, run, state='running'):
    settings = scene.local_depth_reference
    settings.last_run_dir = str(run)
    settings.job_state = state
    settings.job_progress = .58
    settings.is_running = True
    settings.job_status = 'Stale 58%'
    return settings


def main():
    global CHILD
    check('background only', bpy.app.background)
    check('isolated outputs', OUT.is_relative_to((ROOT/'test-output').resolve()))
    scripts = Path(os.environ['BLENDER_USER_SCRIPTS']).resolve()
    check('isolated scripts', scripts.is_relative_to((ROOT/'test-output').resolve()))
    CHILD = subprocess.Popen([str(args.python_executable), '-c', 'import time; time.sleep(120)'],
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    sys.path.insert(0, str(ROOT))
    addon = importlib.import_module('local_depth_reference')
    addon.register()
    ops = addon.operators
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.frame_start, scene.frame_end = -7, 313
    scene.render.fps, scene.render.fps_base = 30, 1.001
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    scene.frame_set(37, subframe=.25)
    camera = scene.camera
    camera.keyframe_insert('location', frame=1)
    camera.location.x += .3
    camera.keyframe_insert('location', frame=21)
    bpy.context.view_layer.update()
    original = (scene.frame_start, scene.frame_end, scene.frame_current, scene.frame_subframe,
                scene.render.fps, scene.render.fps_base, scene.render.resolution_x, scene.render.resolution_y,
                scene.camera, tuple(tuple(row) for row in camera.matrix_world),
                [(c.data_path, c.array_index, tuple(tuple(k.co) for k in c.keyframe_points))
                 for c in camera.animation_data.action.fcurves])
    complete = run_fixture(1, complete=True)
    settings = record(scene, complete, state='complete')
    # Status recovery may read commit metadata, never scan full stream payloads.
    with patch.object(addon.shot_paths, 'load_package', side_effect=AssertionError('Heavy validation on status timer')):
        ops.start_status_monitor()
    check('monitor timer registered', bpy.app.timers.is_registered(ops._poll_status))
    check('load handler registered', ops._status_load_post in bpy.app.handlers.load_post)
    check('first scan includes stale terminal state', settings.job_state == 'complete' and settings.job_progress == 1)
    check('completed stale 58 is no longer running', not settings.is_running)
    check('committed base manifest recovered', settings.manifest_path == str(complete/'manifest.json'))

    active = run_fixture(2)
    active_scene = bpy.data.scenes.new('Status active scene')
    active_settings = record(active_scene, active)
    active_settings.is_running = False
    ops._poll_status()
    check('new scene attaches to live worker', active_settings.is_running and active_settings.job_state == 'running')
    check('active progress retained', abs(active_settings.job_progress-.58) < 1e-6)
    active_settings.is_running = False
    with bpy.context.temp_override(scene=active_scene):
        check('manual refresh reattaches live worker', bpy.ops.ldr.refresh_status() == {'FINISHED'} and active_settings.is_running)

    selection = run_fixture(3, status='awaiting_subject', fraction=.32)
    selected = record(bpy.data.scenes.new('Status selection scene'), selection)
    ops._poll_status()
    check('subject pause recovered', selected.needs_subject and not selected.is_running and selected.job_state == 'awaiting_subject')
    failed = run_fixture(4, status='error', fraction=.58)
    errored = record(bpy.data.scenes.new('Status error scene'), failed)
    ops._poll_status()
    check('worker error recovered', not errored.is_running and errored.job_state == 'error')
    check('failure does not invent completion', errored.manifest_path == '' and abs(errored.job_progress-.58) < 1e-6)

    ae = run_fixture(5, kind='ae_export', complete=True)
    ae_settings = record(bpy.data.scenes.new('Status AE scene'), ae)
    ae_settings.manifest_path = str(complete/'manifest.json')
    ops._poll_status()
    check('AE recovery uses separate manifest', ae_settings.ae_manifest_path == str(ae/'manifest.json') and ae_settings.manifest_path == str(complete/'manifest.json'))

    before = (active_settings.job_state, active_settings.job_progress, active_settings.is_running)
    (active/'progress.json').write_text('{ partial', encoding='utf-8')
    check('transient invalid JSON keeps timer alive', ops._poll_status() == 1.0)
    check('transient read preserves known state', (active_settings.job_state, active_settings.job_progress, active_settings.is_running) == before)
    write(active/'progress.json', {'status': 'running', 'fraction': .8, 'stage': 'export'})
    ops._poll_status()
    check('transient read retries successfully', active_settings.job_stage == 'export' and abs(active_settings.job_progress-.8) < 1e-6)

    class ModalFixture(ops._JobModal):
        def report(self, *args): pass

    old_modal = ModalFixture()
    old_modal._scene = active_scene
    ops._ACTIVE.add(old_modal)
    ops.shutdown(bpy.context)
    check('shutdown unregisters timer', not bpy.app.timers.is_registered(ops._poll_status))
    check('shutdown removes load handler', ops._status_load_post not in bpy.app.handlers.load_post)
    check('shutdown detaches modal', old_modal._detached and not ops._ACTIVE)
    check('shutdown leaves external worker alive', CHILD.poll() is None)
    check('shutdown does not create cancellation flag', not (active/'cancel.flag').exists())
    check('shutdown preserves active job state', active_settings.is_running)
    check('detached next event exits without cancellation', old_modal.modal(bpy.context, SimpleNamespace(type='ESC')) == {'CANCELLED'})
    old_modal.cancel(bpy.context)
    check('implicit cancellation after detach is harmless', not (active/'cancel.flag').exists())
    check('stopped timer returns no interval', ops._poll_status() is None)

    ops.start_status_monitor()
    addon.unregister()
    check('disable leaves worker alive', CHILD.poll() is None and not (active/'cancel.flag').exists())
    check('disable removes RNA', not hasattr(bpy.types.Scene, 'local_depth_reference'))
    addon.register()
    check('reenable starts status monitor', bpy.app.timers.is_registered(ops._poll_status))
    active_settings = active_scene.local_depth_reference
    check('reenable recovers recorded active run', active_settings.last_run_dir == str(active) and active_settings.is_running)

    explicit = run_fixture(6)
    record(scene, explicit)
    check('explicit Cancel operator succeeds', bpy.ops.ldr.cancel_generation() == {'FINISHED'})
    check('explicit Cancel writes cancellation flag', (explicit/'cancel.flag').is_file())
    escape = run_fixture(7)
    record(scene, escape)
    modal = ModalFixture()
    modal._scene = scene
    check('explicit ESC keeps modal alive until worker stops', modal.modal(bpy.context, SimpleNamespace(type='ESC')) == {'RUNNING_MODAL'})
    check('explicit ESC writes cancellation flag', (escape/'cancel.flag').is_file())

    # Save/reopen a stale terminal state: load_post must force a fresh first scan.
    settings = record(scene, complete, state='complete')
    ops._poll_status()
    snapshot_path = OUT/'status_lifecycle.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(snapshot_path))
    scene_name = scene.name
    write(complete/'manifest.json', {'schema': 1, 'type': 'local-depth-reference', 'status': 'incomplete'})
    write(complete/'progress.json', {'status': 'error', 'fraction': .58, 'message': 'Fixture changed after save'})
    ops._poll_status()
    check('known terminal scene skips idle rereads', settings.job_state == 'complete')
    bpy.ops.wm.open_mainfile(filepath=str(snapshot_path))
    check('persistent timer survives file load', bpy.app.timers.is_registered(ops._poll_status))
    ops._poll_status()
    scene = bpy.data.scenes[scene_name]
    settings = scene.local_depth_reference
    check('file load recovers scene status afresh', settings.job_state == 'error' and not settings.is_running)
    check('file load remains Cycles', scene.render.engine == 'CYCLES')
    camera = scene.camera
    current = (scene.frame_start, scene.frame_end, scene.frame_current, scene.frame_subframe,
               scene.render.fps, scene.render.fps_base, scene.render.resolution_x, scene.render.resolution_y,
               scene.camera, tuple(tuple(row) for row in camera.matrix_world),
               [(c.data_path, c.array_index, tuple(tuple(k.co) for k in c.keyframe_points))
                for c in camera.animation_data.action.fcurves])
    # Object pointer identity changes on reopen; compare camera name separately.
    check('status lifecycle preserves scene timing and dimensions', current[:8] == original[:8])
    check('status lifecycle preserves camera transform and keys', current[9:] == original[9:])
    check('external worker remains alive through all UI lifecycle tests', CHILD.poll() is None)
    addon.unregister()
    REPORT['status'] = 'passed'


try:
    main()
except Exception:
    REPORT['status'] = 'failed'
    REPORT['error'] = traceback.format_exc()
    traceback.print_exc()
finally:
    if CHILD is not None:
        CHILD.terminate()
        CHILD.wait(timeout=10)
    (OUT/'status_lifecycle_report.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')
    print('STATUS_LIFECYCLE '+json.dumps({'status': REPORT['status'], 'checks': len(REPORT['checks'])}))
if REPORT['status'] != 'passed':
    raise RuntimeError('Status lifecycle test failed')

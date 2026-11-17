"""Isolated Blender 4.2 release ZIP/UI smoke test; no inference or production writes."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from unittest.mock import patch

import bpy


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--runtime-config', required=True)
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:])


ARGS = parse_args()
OUT = Path(ARGS.output).resolve()
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {'checks': [], 'zip': str(Path(ARGS.zip).resolve()),
          'zip_sha256': hashlib.sha256(Path(ARGS.zip).read_bytes()).hexdigest(),
          'blender': bpy.app.version_string, 'manifest': ARGS.manifest}


def check(name, condition, detail=None):
    REPORT['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
    if not condition:
        raise AssertionError(name + ': ' + str(detail))


def finished(result):
    return result == {'FINISHED'}


def rejected(operation):
    try:
        return operation() == {'CANCELLED'}
    except RuntimeError:
        return True  # bpy propagates operator ERROR reports as RuntimeError.


def curve_snapshot(data):
    animation = data.animation_data
    action = animation.action if animation else None
    return None if action is None else {
        'name': action.name,
        'curves': [(curve.data_path, curve.array_index,
                    [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right), point.interpolation)
                     for point in curve.keyframe_points]) for curve in action.fcurves]}


def scene_snapshot(scene, camera, rig):
    r = scene.render
    return {'render': [r.engine, r.fps, r.fps_base, r.resolution_x, r.resolution_y,
                       r.resolution_percentage, r.pixel_aspect_x, r.pixel_aspect_y],
            'frames': [scene.frame_start, scene.frame_end, scene.frame_current, scene.frame_subframe],
            'camera': scene.camera.name, 'selected': sorted(obj.name for obj in bpy.context.selected_objects),
            'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
            'camera_data': [(key, getattr(camera.data, key)) for key in
                            ('type', 'lens', 'sensor_fit', 'sensor_width', 'sensor_height', 'shift_x', 'shift_y', 'clip_start', 'clip_end')],
            'camera_matrix': [list(row) for row in camera.matrix_world],
            'camera_animation': curve_snapshot(camera), 'rig_animation': curve_snapshot(rig),
            'rig_matrix': [list(row) for row in rig.matrix_world],
            'rig_bones': [(bone.name, tuple(bone.location), tuple(bone.rotation_euler), tuple(bone.scale))
                          for bone in rig.pose.bones]}


def main():
    expected_scripts = (OUT / 'package-user' / 'scripts').resolve()
    check('isolated user scripts', Path(os.environ['BLENDER_USER_SCRIPTS']).resolve() == expected_scripts)
    check('isolated native extension dependencies', Path(bpy.utils.user_resource('EXTENSIONS')).resolve() == (OUT/'package-user/extensions').resolve())
    check('ZIP install', finished(bpy.ops.preferences.addon_install(filepath=ARGS.zip, overwrite=True)))
    check('ZIP enable', finished(bpy.ops.preferences.addon_enable(module='local_depth_reference')))
    addon = importlib.import_module('local_depth_reference')
    module_path = Path(addon.__file__).resolve()
    check('loaded installed ZIP code', module_path.is_relative_to(expected_scripts), str(module_path))
    check('disable', finished(bpy.ops.preferences.addon_disable(module='local_depth_reference')))
    check('unregister removed scene RNA', not hasattr(bpy.types.Scene, 'local_depth_reference'))
    check('reregister', finished(bpy.ops.preferences.addon_enable(module='local_depth_reference')))
    check('reregister restored scene RNA', hasattr(bpy.types.Scene, 'local_depth_reference'))
    geometry, runtime = addon.geometry, importlib.import_module('local_depth_reference.runtime')

    config = json.loads(Path(ARGS.runtime_config).read_text(encoding='utf-8-sig'))
    config.pop('worker', None)
    config_path = OUT / 'runtime-without-worker.json'
    config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    loaded = runtime.load_config(config_path)
    bundled = module_path.parent / 'worker' / 'process.py'
    check('worker fallback uses installed bundled worker', Path(loaded['worker']) == bundled)
    external_python = Path(sys.prefix) / 'bin' / ('python.exe' if os.name == 'nt' else 'python3')
    check('bundled standalone Python exists', external_python.is_file(), str(external_python))
    # Direct CLI/worker help must import using standard library, not Blender/torch.
    for label, path, arguments in (
        ('installed CLI configure', module_path.parent / 'cli.py', ['configure', '--config', str(config_path), '--check']),
        ('installed CLI validate real package', module_path.parent / 'cli.py', ['validate', '--manifest', ARGS.manifest]),
        ('installed worker stdlib entrypoint', bundled, ['--help']),
    ):
        run = subprocess.run([str(external_python), str(path), *arguments], capture_output=True, text=True, timeout=180)
        check(label, run.returncode == 0, {'returncode': run.returncode, 'stderr': run.stderr[-1500:]})

    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.render.fps = 30
    scene.render.fps_base = 1.001
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    scene.render.resolution_percentage = 63
    scene.render.pixel_aspect_x, scene.render.pixel_aspect_y = 1.2, .9
    scene.frame_start, scene.frame_end = -5, 230
    camera = scene.camera
    camera.data.lens = 48
    camera.data.sensor_fit = 'VERTICAL'
    camera.data.sensor_width, camera.data.sensor_height = 32, 18
    camera.data.shift_x, camera.data.shift_y = .12, -.08
    camera.keyframe_insert('location', frame=1)
    camera.location.x += .25
    camera.keyframe_insert('location', frame=29)
    bpy.ops.object.armature_add()
    rig = bpy.context.object
    rig.name = 'Artist Rig Preserved'
    bone = rig.pose.bones[0]
    bone.rotation_mode = 'XYZ'
    bone.rotation_euler.z = .2
    bone.keyframe_insert('rotation_euler', frame=1)
    bone.rotation_euler.z = -.35
    bone.keyframe_insert('rotation_euler', frame=29)
    scene.frame_set(17, subframe=.25)
    bpy.context.view_layer.update()
    settings = scene.local_depth_reference
    settings.main_camera = camera
    settings.start_frame = 1
    original = scene_snapshot(scene, camera, rig)
    check('first operator import', finished(bpy.ops.ldr.import_manifest('EXEC_DEFAULT', filepath=ARGS.manifest)))
    first = settings.import_collection
    a = geometry.get_import_objects(first)
    check('default offset is applied as one-based index', a['controls']['Frame Index Offset'] == 1)
    check('first import preserves artist scene', scene_snapshot(scene, camera, rig) == original)
    a['controls']['Grid Resolution'] = 33
    a['controls'].update_tag()
    check('second operator import', finished(bpy.ops.ldr.import_manifest('EXEC_DEFAULT', filepath=ARGS.manifest)))
    second = settings.import_collection
    b = geometry.get_import_objects(second)
    b['controls']['Grid Resolution'] = 33
    b['controls'].update_tag()
    check('independent ownership', first[geometry.OWNER] != second[geometry.OWNER])
    check('independent geometry groups', a['surface'].modifiers[0].node_group != b['surface'].modifiers[0].node_group)
    settings.import_collection = first
    check('offset first', finished(bpy.ops.ldr.offset_frames(delta=2)))
    check('offset does not change other import', a['controls']['Frame Index Offset'] == 3 and b['controls']['Frame Index Offset'] == 1)
    settings.import_collection = second
    check('offset second', finished(bpy.ops.ldr.offset_frames(delta=-2)))
    check('second offset independent', a['controls']['Frame Index Offset'] == 3 and b['controls']['Frame Index Offset'] == -1)
    settings.import_collection = first
    a['camera'].data.lens = 13
    check('match camera operator', finished(bpy.ops.ldr.match_camera_settings()))
    check('camera properties matched', all(getattr(a['camera'].data, key) == getattr(camera.data, key) for key in geometry.CAMERA_FIELDS))
    a['camera'].location.x += 2
    check('align and parent operator', finished(bpy.ops.ldr.align_and_parent()))
    bpy.context.view_layer.update()
    check('reference camera parent and transform', a['camera'].parent == camera and
          all(abs(a['camera'].matrix_world[i][j] - camera.matrix_world[i][j]) < 1e-6 for i in range(4) for j in range(4)))
    check('restore first', finished(bpy.ops.ldr.restore_original()))
    check('restore first scoped', a['controls']['Frame Index Offset'] == 1 and b['controls']['Frame Index Offset'] == -1 and a['camera'].parent is None)
    a['controls']['Grid Resolution'] = 33
    a['controls'].update_tag()
    settings.import_collection = second
    check('restore second', finished(bpy.ops.ldr.restore_original()))
    check('restore second independent', b['controls']['Frame Index Offset'] == 1)
    b['controls']['Grid Resolution'] = 33
    b['controls'].update_tag()
    foreign = bpy.data.collections.new('Unrelated Artist Collection')
    scene.collection.children.link(foreign)
    settings.import_collection = foreign
    for name in ('match_camera_settings', 'align_and_parent', 'offset_frames', 'restore_original'):
        check('reject foreign collection ' + name, rejected(getattr(bpy.ops.ldr, name)))
    settings.import_collection = first
    check('all import controls preserve artist scene', scene_snapshot(scene, camera, rig) == original)

    # UI operator lifecycle only: no file mutation or GPU work in real shot.
    fake_run = str(OUT / 'mock-run')
    calls = []
    state = {'status': 'running', 'fraction': .37, 'stage': 'depth', 'message': 'Mock inference'}
    def create_job(**kwargs):
        calls.append(('create', kwargs))
        return {'job_path': fake_run + '/job.json', 'run_dir': fake_run}
    def launch_job(path):
        calls.append(('launch', path))
        return {'run_dir': fake_run}
    def resume_job(path, **kwargs):
        calls.append(('resume', path, kwargs))
        return {'run_dir': fake_run}
    def request_cancel(path):
        calls.append(('cancel', path))
    settings.footage_path = str(OUT / 'mock-source.mp4')
    settings.shot_folder = str(OUT / 'mock-shot')
    with patch.multiple(runtime, create_job=create_job, launch_job=launch_job, resume_job=resume_job,
                        request_cancel=request_cancel, read_progress=lambda run: dict(state)):
        check('generate mocked worker', finished(bpy.ops.ldr.generate()))
        check('generate passes exact source and shot', calls[0][1]['source'] == settings.footage_path and calls[0][1]['shot_dir'] == settings.shot_folder)
        check('generate records running', settings.is_running and settings.last_run_dir == fake_run)
        check('refresh running', finished(bpy.ops.ldr.refresh_status()) and abs(settings.job_progress - .37) < 1e-5)
        check('cancel mocked worker', finished(bpy.ops.ldr.cancel_generation()) and calls[-1] == ('cancel', fake_run))
        state.update(status='awaiting_subject', subject_selection={'preview': 'subject_preview.jpg'}, message='Choose person')
        check('refresh selection', finished(bpy.ops.ldr.refresh_status()) and settings.needs_subject and not settings.is_running)
        check('preview path bound to run', Path(settings.preview_path) == Path(fake_run) / 'subject_preview.jpg')
        settings.subject_id = ''
        check('missing subject rejected', rejected(bpy.ops.ldr.resume_generation))
        settings.subject_id = '7'
        check('resume subject operator', finished(bpy.ops.ldr.resume_generation()))
        check('resume uses actual entered ID', calls[-1] == ('resume', fake_run, {'subject_id': '7', 'subject_points': None}))
        state.update(status='complete', manifest_path=ARGS.manifest)
        check('refresh complete', finished(bpy.ops.ldr.refresh_status()) and not settings.is_running and settings.job_progress == 1)
        state.update(status='error', message='Mock failure')
        check('refresh failure state', finished(bpy.ops.ldr.refresh_status()) and settings.job_state == 'error')
    check('mock lifecycle preserves artist scene', scene_snapshot(scene, camera, rig) == original)
    settings.manifest_path = ARGS.manifest
    settings.last_run_dir = str(Path(ARGS.manifest).parent)
    settings.footage_path = ''
    settings.shot_folder = ''
    settings.job_state, settings.job_status = 'complete', 'Package smoke test complete'
    settings.main_camera = camera
    settings.import_collection = first
    scene.render.engine = 'CYCLES'
    blend = OUT / 'package_smoke.blend'
    check('save isolated Cycles test blend', finished(bpy.ops.wm.save_as_mainfile(filepath=str(blend))))
    REPORT.update(status='passed', blend=str(blend), installed_module=str(module_path), bundled_worker=str(bundled))


try:
    main()
except Exception:
    REPORT['status'] = 'failed'
    REPORT['error'] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / 'package_smoke_report.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')
    print('PACKAGE_SMOKE_RESULT ' + json.dumps({'status': REPORT['status'], 'checks': len(REPORT['checks'])}))
if REPORT['status'] != 'passed':
    raise RuntimeError('Package smoke test failed; see package_smoke_report.json')

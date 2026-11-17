"""Background-only Blender UI regression tests. Never launch the actual inference/export worker."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'test-output/ae-exports-blender')
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {'checks': [], 'blender': bpy.app.version_string}


def check(name, condition):
    REPORT['checks'].append({'name': name, 'passed': bool(condition)})
    if not condition: raise AssertionError(name)


def finished(result):
    return result == {'FINISHED'}


def rejected(call):
    try: return call() == {'CANCELLED'}
    except RuntimeError: return True


def snapshot(scene, camera, rig):
    def curves(data):
        action=data.animation_data.action if data.animation_data else None
        return None if not action else [(c.data_path,c.array_index,[(tuple(p.co),p.interpolation) for p in c.keyframe_points]) for c in action.fcurves]
    return {'render': [scene.render.engine,scene.render.fps,scene.render.fps_base,scene.render.resolution_x,scene.render.resolution_y,
                       scene.render.resolution_percentage,scene.render.pixel_aspect_x,scene.render.pixel_aspect_y],
            'timeline':[scene.frame_start,scene.frame_end,scene.frame_current,scene.frame_subframe],
            'camera':scene.camera.name,'camera_data':[camera.data.lens,camera.data.sensor_fit,camera.data.sensor_width,camera.data.sensor_height],
            'camera_matrix':[list(row) for row in camera.matrix_world], 'camera_keys':curves(camera),'rig_keys':curves(rig),
            'selection':sorted(obj.name for obj in bpy.context.selected_objects),
            'active':bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None}


def fixture_package():
    run=OUT/'fixture-base';run.mkdir(exist_ok=True)
    paths={};inventory=[]
    for name in ('raw_depth','clean_depth','alpha','source'):
        folder=run/name;folder.mkdir(exist_ok=True)
        suffix='.exr' if 'depth' in name else '.png'
        image=bpy.data.images.new('Smoke '+name,width=32,height=18,alpha=True,float_buffer=True)
        image.colorspace_settings.name='Non-Color'
        image.pixels[:]=[.5,.5,.5,1]*(32*18)
        image.file_format='OPEN_EXR' if suffix=='.exr' else 'PNG'
        for index in range(3):
            path=folder/f'{index:05d}{suffix}'
            image.filepath_raw=str(path);image.save()
            inventory.append({'path':path.relative_to(run).as_posix(),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        bpy.data.images.remove(image)
        paths[name]=f'{name}/00000{suffix}'
    data={'schema':1,'type':'local-depth-reference','status':'complete',
          'source':{'path':str(OUT/'missing-original.mp4'),'width':64,'height':36,'frame_count':3,'fps_num':24,'fps_den':1},
          'processing':{'working_width':32,'working_height':18,'q_median':1},
          'sequence':{'first_index':0,'count':3,'digits':5},'paths':paths,'binding':{},'stage_cache':{'fixture':{'artifacts':inventory}}}
    manifest=run/'manifest.json';manifest.write_text(json.dumps(data))
    ae=OUT/'ae-only-manifest.json';ae.write_text(json.dumps({**data,'type':'local-depth-ae-export'}))
    return manifest,ae


class LayoutRecorder:
    def __init__(self, events): self.events=events
    def __getattr__(self, name):
        def call(*args,**kwargs):
            self.events.append((name,args,kwargs))
            return LayoutRecorder(self.events)
        return call


def main():
    check('background process only',bpy.app.background)
    scripts=os.environ.get('BLENDER_USER_SCRIPTS','')
    check('isolated user scripts configured',bool(scripts) and Path(scripts).resolve().is_relative_to((ROOT/'test-output').resolve()))
    check('isolated output',OUT.is_relative_to((ROOT/'test-output').resolve()))
    sys.path.insert(0,str(ROOT))
    addon=importlib.import_module('local_depth_reference');addon.register()
    runtime=importlib.import_module('local_depth_reference.runtime')
    check('new coverage property registered',hasattr(bpy.context.scene.local_depth_reference,'ae_coverage'))
    check('coverage default both',bpy.context.scene.local_depth_reference.ae_coverage=='both')
    identifiers={item.identifier for item in bpy.context.scene.local_depth_reference.bl_rna.properties['ae_coverage'].enum_items}
    check('all coverage choices registered',identifiers=={'both','whole_scene','person'})
    check('base job default preserved',bpy.context.scene.local_depth_reference.job_kind=='depth')
    check('new AE output property registered',hasattr(bpy.context.scene.local_depth_reference,'ae_manifest_path'))
    addon.unregister();check('unregister cleans scene RNA',not hasattr(bpy.types.Scene,'local_depth_reference'))
    addon.register();check('reregister restores operator',hasattr(bpy.ops.ldr,'export_ae'))
    manifest,ae_manifest=fixture_package()
    scene=bpy.context.scene;scene.render.engine='CYCLES';scene.render.fps=30;scene.render.fps_base=1.001
    scene.render.resolution_x,scene.render.resolution_y=1080,1920;scene.render.resolution_percentage=47
    scene.frame_start,scene.frame_end=-3,201
    camera=scene.camera;camera.data.lens=48;camera.data.sensor_fit='VERTICAL'
    camera.keyframe_insert('location',frame=1);camera.location.x+=.25;camera.keyframe_insert('location',frame=21)
    bpy.ops.object.armature_add();rig=bpy.context.object;rig.name='Preserved Artist Rig'
    rig.pose.bones[0].location.z=.2;rig.pose.bones[0].keyframe_insert('location',frame=1)
    rig.pose.bones[0].location.z=.6;rig.pose.bones[0].keyframe_insert('location',frame=21)
    scene.frame_set(11,subframe=.25);bpy.context.view_layer.update()
    settings=scene.local_depth_reference;settings.main_camera=camera;settings.manifest_path=str(manifest)
    baseline=snapshot(scene,camera,rig)
    check('existing Blender import still works',finished(bpy.ops.ldr.import_manifest(filepath=str(manifest))))
    imported=settings.import_collection
    objects=addon.geometry.get_import_objects(imported);objects['controls']['Grid Resolution']=33;objects['controls'].update_tag()
    check('existing import preserves artist state',snapshot(scene,camera,rig)==baseline)
    before_objects={obj.name for obj in bpy.data.objects}
    check('AE export manifest rejected by Blender import',rejected(lambda:bpy.ops.ldr.import_manifest(filepath=str(ae_manifest))))
    check('rejected AE import creates no objects',before_objects=={obj.name for obj in bpy.data.objects})
    check('rejected AE import preserves base selection',settings.import_collection==imported and settings.manifest_path==str(manifest))
    events=[];addon.ui.LDR_PT_reference.draw(SimpleNamespace(layout=LayoutRecorder(events)),bpy.context)
    check('UI exposes separate AE action',any(kind=='operator' and arguments and arguments[0]=='ldr.export_ae' for kind,arguments,_ in events))
    check('UI exposes coverage',any(kind=='prop' and len(arguments)>1 and arguments[1]=='ae_coverage' for kind,arguments,_ in events))
    calls=[];run=str(OUT/'mock-ae-run');state={'status':'running','job_kind':'ae_export','fraction':.25}
    def export(manifest_path,coverage='both',config_path=''):
        calls.append(('export',str(manifest_path),coverage));return {'job_path':run+'/job.json','run_dir':run,'kind':'ae_export'}
    def launch(job_path):calls.append(('launch',str(job_path)));return {'run_dir':run}
    def cancel(run_dir):calls.append(('cancel',str(run_dir)))
    def resume(run_dir,**kwargs):calls.append(('resume',str(run_dir),kwargs));return {'run_dir':run}
    with patch.multiple(runtime,create_ae_export=export,launch_job=launch,request_cancel=cancel,resume_job=resume,read_progress=lambda run_dir:dict(state)):
        for coverage in ('both','whole_scene','person'):
            settings.is_running=False;settings.ae_coverage=coverage
            check('dispatch coverage '+coverage,finished(bpy.ops.ldr.export_ae()))
            check('dispatch correct source '+coverage,calls[-2]==('export',str(manifest),coverage))
            check('AE job kind set '+coverage,settings.job_kind=='ae_export')
            check('refresh export progress '+coverage,finished(bpy.ops.ldr.refresh_status()) and abs(settings.job_progress-.25)<1e-6)
            check('base manifest unchanged '+coverage,settings.manifest_path==str(manifest))
        check('cancel export dispatch',finished(bpy.ops.ldr.cancel_generation()) and calls[-1]==('cancel',run))
        state.update(status='cancelled');check('cancelled export clears running',finished(bpy.ops.ldr.refresh_status()) and not settings.is_running)
        check('cancelled export retains import',settings.import_collection==imported and settings.manifest_path==str(manifest))
        check('resume export dispatch',finished(bpy.ops.ldr.resume_generation()))
        state.update(status='error',message='Fixture export failure');check('failed export clears running',finished(bpy.ops.ldr.refresh_status()) and not settings.is_running)
        check('failure retains Blender manifest',settings.manifest_path==str(manifest))
        state.update(status='complete',ae_manifest_path=str(ae_manifest),manifest_path=str(ae_manifest),fraction=1)
        check('complete export progress',finished(bpy.ops.ldr.refresh_status()))
        check('AE output routed separately',settings.ae_manifest_path==str(ae_manifest) and settings.manifest_path==str(manifest))
        check('AE export never changes current import',settings.import_collection==imported)
    settings.is_running=False
    with patch.object(runtime,'create_ae_export',side_effect=ValueError('Missing or incomplete base package')):
        check('creation failure actionable',rejected(bpy.ops.ldr.export_ae))
        check('creation failure leaves no running state',not settings.is_running)
    settings.footage_path=str(OUT/'source.mp4')
    def generation(**kwargs):calls.append(('generate',kwargs));return {'run_dir':str(OUT/'mock-depth-run'),'job_path':str(OUT/'mock-depth-run/job.json')}
    with patch.multiple(runtime,create_job=generation,launch_job=launch):
        check('base generation operator still dispatches',finished(bpy.ops.ldr.generate()))
        check('base generation resets depth kind',settings.job_kind=='depth')
    settings.is_running=False
    check('all AE actions preserve artist state',snapshot(scene,camera,rig)==baseline)
    check('all AE actions preserve scene object ownership',before_objects=={obj.name for obj in bpy.data.objects})
    scene.render.engine='CYCLES';bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'ae_exports_smoke.blend'))
    addon.unregister();check('final unregister clean',not hasattr(bpy.types.Scene,'local_depth_reference'))
    REPORT.update(status='passed',module=str(Path(addon.__file__).resolve()))


try:
    main()
except Exception:
    REPORT['status']='failed';REPORT['error']=traceback.format_exc();traceback.print_exc()
finally:
    (OUT/'blender_ae_exports_report.json').write_text(json.dumps(REPORT,indent=2))
    print('AE_EXPORT_SMOKE '+json.dumps({'status':REPORT['status'],'checks':len(REPORT['checks'])}))
if REPORT['status']!='passed':raise RuntimeError('AE export Blender smoke failed')

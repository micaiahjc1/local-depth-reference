"""Reproduce a native in-session 0.1.0 -> current ZIP update in isolated Blender."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
import bpy

parser=argparse.ArgumentParser()
parser.add_argument('--old-zip',required=True)
parser.add_argument('--new-zip',required=True)
parser.add_argument('--output',required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'blender':bpy.app.version_string}
def check(name,value):
    report['checks'].append({'name':name,'passed':bool(value)})
    if not value:raise AssertionError(name)
def main():
    check('isolated scripts',Path(os.environ['BLENDER_USER_SCRIPTS']).resolve().is_relative_to(out))
    check('isolated extension dependencies',Path(bpy.utils.user_resource('EXTENSIONS')).resolve().is_relative_to(out))
    check('background only',bpy.app.background)
    check('install old ZIP',bpy.ops.preferences.addon_install(filepath=args.old_zip,overwrite=True)=={'FINISHED'})
    check('enable old ZIP',bpy.ops.preferences.addon_enable(module='local_depth_reference')=={'FINISHED'})
    addon=importlib.import_module('local_depth_reference')
    check('old UI reproduced',not hasattr(bpy.context.scene.local_depth_reference,'ae_coverage'))
    scene=bpy.context.scene;scene.render.engine='CYCLES'
    scene.render.fps=30;scene.render.resolution_x=1234;scene.render.resolution_y=777
    scene.frame_set(23);camera=scene.camera
    fixture=out/'completed-run';fixture.mkdir(exist_ok=True)
    (fixture/'progress.json').write_text(json.dumps({'status':'complete','fraction':1,'message':'Depth reference ready'}))
    (fixture/'manifest.json').write_text(json.dumps({'schema':1,'type':'local-depth-reference','status':'complete'}))
    s=scene.local_depth_reference
    s.footage_path='//Artist plate.mp4';s.shot_folder='//../';s.main_camera=camera;s.start_frame=1001
    s.cleanup_radius=4;s.cleanup_strength=.73;s.last_run_dir=str(fixture)
    s.job_state='running';s.job_stage='depth';s.job_progress=.58;s.job_status='Loading Video Depth Anything Small';s.is_running=True
    prior=(scene.render.engine,scene.render.fps,scene.render.resolution_x,scene.render.resolution_y,scene.frame_current,scene.camera)
    check('install current ZIP in same process',bpy.ops.preferences.addon_install(filepath=args.new_zip,overwrite=True)=={'FINISHED'})
    check('enable current ZIP in same process',bpy.ops.preferences.addon_enable(module='local_depth_reference')=={'FINISHED'})
    addon=sys.modules['local_depth_reference'];s=scene.local_depth_reference
    check('automatic progress timer registered',bpy.app.timers.is_registered(addon.operators._poll_status))
    # Background scripts do not run a UI event loop while executing. Invoke
    # the registered callback once to exercise the deferred UI tick.
    addon.operators._poll_status()
    check('new version loaded',addon.bl_info['version']==(0,3,0))
    check('new child UI code loaded','ae_coverage' in addon.ui.LDR_Settings.__annotations__)
    check('AE RNA available',hasattr(s,'ae_coverage'))
    check('matte source RNA available',hasattr(s,'matte_source') and hasattr(s,'matte_path') and hasattr(s,'matte_channel') and hasattr(s,'matte_invert'))
    check('automatic matting remains default',s.matte_source=='automatic')
    check('AE operator available',hasattr(bpy.ops.ldr,'export_ae'))
    check('new panel draws AE section','After Effects export' in addon.ui.LDR_PT_reference.draw.__code__.co_consts)
    check('input settings survive upgrade',s.footage_path=='//Artist plate.mp4' and s.shot_folder=='//../' and s.start_frame==1001)
    check('camera pointer survives upgrade',s.main_camera==camera)
    check('cleanup settings survive upgrade',s.cleanup_radius==4 and abs(s.cleanup_strength-.73)<1e-6)
    check('completed job automatically recovered',s.job_state=='complete' and s.job_progress==1 and not s.is_running)
    check('completed manifest recovered',Path(s.manifest_path)==fixture/'manifest.json')
    check('native scene preserved',prior==(scene.render.engine,scene.render.fps,scene.render.resolution_x,scene.render.resolution_y,scene.frame_current,scene.camera))
    check('no cancellation requested',(fixture/'cancel.flag').exists() is False)
    bpy.ops.preferences.addon_disable(module='local_depth_reference')
    check('monitor stopped',not bpy.app.timers.is_registered(addon.operators._poll_status))
    report.update(status='passed',loaded_module=str(addon.__file__))
try:main()
except Exception:
    report.update(status='failed',error=traceback.format_exc());traceback.print_exc()
finally:
    (out/'upgrade_report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'status':report['status'],'checks':len(report['checks'])}))
if report['status']!='passed':raise RuntimeError('In-session upgrade failed')

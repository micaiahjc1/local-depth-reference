"""Native Blender4.2 supplied-alpha import/geometry/render qualification."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np
from mathutils import Matrix

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--fixture-report',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=True)
REPORT={'checks':[],'samples':[],'blender':bpy.app.version_string,'renders':[]}


def check(name,ok,**extra):
    REPORT['checks'].append({'name':name,'passed':bool(ok),**extra})
    if not ok:raise AssertionError(name)


def snapshot(scene,camera,rig):
    def curves(owner):
        action=owner.animation_data.action if owner.animation_data else None
        return None if action is None else [(f.data_path,f.array_index,[(tuple(k.co),k.interpolation) for k in f.keyframe_points]) for f in action.fcurves]
    return {'render':[scene.render.engine,scene.render.fps,scene.render.fps_base,scene.render.resolution_x,scene.render.resolution_y,
                      scene.render.resolution_percentage,scene.render.pixel_aspect_x,scene.render.pixel_aspect_y],
            'timeline':[scene.frame_start,scene.frame_end,scene.frame_current,scene.frame_subframe],
            'camera':scene.camera.name,'camera_data':[camera.data.lens,camera.data.sensor_fit,camera.data.sensor_width,camera.data.sensor_height],
            'camera_matrix':[list(row) for row in camera.matrix_world],'camera_keys':curves(camera),'rig_keys':curves(rig),
            'selection':sorted(o.name for o in bpy.context.selected_objects),
            'active':bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None}


def bilinear(values,uv):
    h,w=values.shape
    x=np.clip(uv[:,0]*w-.5,0,w-1);y=np.clip((1-uv[:,1])*h-.5,0,h-1)
    x0=np.floor(x).astype(int);y0=np.floor(y).astype(int);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    dx=x-x0;dy=y-y0
    return values[y0,x0]*(1-dx)*(1-dy)+values[y0,x1]*dx*(1-dy)+values[y1,x0]*(1-dx)*dy+values[y1,x1]*dx*dy


class LayoutRecorder:
    def __init__(self,events):self.events=events
    def __getattr__(self,name):
        def call(*args,**kwargs):self.events.append((name,args,kwargs));return LayoutRecorder(self.events)
        return call


def main():
    check('background Blender only',bpy.app.background)
    for env,resource in [('BLENDER_USER_SCRIPTS','SCRIPTS'),('BLENDER_USER_CONFIG','CONFIG'),('BLENDER_USER_DATAFILES','DATAFILES'),('BLENDER_USER_EXTENSIONS','EXTENSIONS')]:
        configured=os.environ.get(env)
        check('isolated '+env,bool(configured) and Path(configured).resolve().is_relative_to(OUT))
        actual=bpy.utils.user_resource(resource)
        check('native isolated '+resource,bool(actual) and Path(actual).resolve().is_relative_to(OUT),path=actual)
    fixture=json.loads(args.fixture_report.read_text())
    check('fixture export checks passed',fixture['status']=='passed')
    sys.path.insert(0,str(ROOT))
    addon=importlib.import_module('local_depth_reference');addon.register()
    from local_depth_reference.shot_paths import load_package
    g=addon.geometry
    s=bpy.context.scene;s.render.engine='CYCLES';s.render.fps=30;s.render.fps_base=1.001
    s.render.resolution_x=1920;s.render.resolution_y=1080;s.render.resolution_percentage=47
    s.frame_start=-3;s.frame_end=211
    camera=s.camera;camera.matrix_world=Matrix.Identity(4);camera.data.lens=47;camera.data.sensor_fit='VERTICAL'
    camera.location.x=.0;camera.keyframe_insert('location',frame=1)
    camera.location.x=.25;camera.keyframe_insert('location',frame=21)
    bpy.ops.object.armature_add();rig=bpy.context.object;rig.name='Preserved Artist Rig'
    rig.pose.bones[0].location.z=.25;rig.pose.bones[0].keyframe_insert('location',frame=1)
    rig.pose.bones[0].location.z=.55;rig.pose.bones[0].keyframe_insert('location',frame=21)
    s.frame_set(7,subframe=.25);bpy.context.view_layer.update();baseline=snapshot(s,camera,rig)
    settings=s.local_depth_reference
    check('matte source UI registered',hasattr(settings,'matte_source'))
    check('automatic generation remains default',settings.matte_source=='automatic')
    for prop in ('matte_path','matte_channel','matte_invert'):
        check('UI property '+prop,hasattr(settings,prop))
    enum={v.identifier for v in settings.bl_rna.properties['matte_source'].enum_items}
    check('automatic and custom options available',enum=={'automatic','custom'})
    calls=[]
    def create(**kwargs):calls.append(kwargs);return {'run_dir':str(OUT/'mock-run'),'job_path':str(OUT/'mock-run/job.json')}
    settings.footage_path=str(OUT/'source.mp4');settings.shot_folder=str(OUT)
    runtime=importlib.import_module('local_depth_reference.runtime')
    with patch.multiple(runtime,create_job=create,launch_job=lambda p:{'run_dir':str(OUT/'mock-run')}):
        for mode in ('automatic','custom'):
            settings.is_running=False;settings.matte_source=mode;settings.matte_path=str(OUT/'matte_1001.png');settings.matte_channel='alpha';settings.matte_invert=True
            check(mode+' generate operator dispatches',bpy.ops.ldr.generate()=={'FINISHED'})
            opts=calls[-1]['settings']
            check(mode+' choice reaches worker settings',opts['matte_source']==mode)
            if mode=='custom':check('custom path/channel/invert reach worker',opts['matte_path']==settings.matte_path and opts['matte_channel']=='alpha' and opts['matte_invert'] is True)
    settings.is_running=False
    settings.matte_source='custom';events=[]
    addon.ui.LDR_PT_reference.draw(SimpleNamespace(layout=LayoutRecorder(events)),bpy.context)
    for prop in ('matte_source','matte_path','matte_channel','matte_invert'):
        check('panel draws '+prop,any(k=='prop' and len(a)>1 and a[1]==prop for k,a,_ in events))
    check('generation dispatch preserves artist state',snapshot(s,camera,rig)==baseline)
    imported=[]
    for item in fixture['packages']:
        package=load_package(item['manifest']);col=g.create_import(bpy.context,package,camera,start_frame=101)
        check(item['label']+' import preserves artist state',snapshot(s,camera,rig)==baseline)
        col.name='QA '+item['label'];objects=g.get_import_objects(col);control=objects['controls']
        control['Grid Resolution']=65;control['Blur Iterations']=0;control['Matte Threshold']=.01;control.update_tag()
        truth=np.load(item['truth']);imported.append((item,col,objects,truth))
        check(item['label']+' alpha image uses data color',all(img.colorspace_settings.name=='Non-Color' for img in bpy.data.images if img.filepath==package['streams']['alpha']))
    # All imported objects remain independently owned; evaluate each frame directly.
    for item,col,objects,truth in imported:
        control=objects['controls'];surface=objects['surface']
        alpha=truth['alpha'].astype(np.float64)/(255 if truth['alpha'].dtype==np.uint8 else 65535)
        for frame in (0,1,2,3,4):
            s.frame_set(101+frame);bpy.context.view_layer.update();mesh=surface.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
            if frame==3:
                check(item['label']+' empty matte makes empty geometry',len(mesh.vertices)==0)
                continue
            co=np.empty((len(mesh.vertices),3),np.float32);uv=co.copy()
            mesh.vertices.foreach_get('co',co.reshape(-1));mesh.attributes[g.UV_NAME].data.foreach_get('vector',uv.reshape(-1))
            expected=bilinear(truth['clean'][frame],uv)
            error=float(np.max(np.abs(-co[:,2]-expected)))
            check(item['label']+f' cleaned depth samples frame{frame}',len(co)>100 and error<2e-5,error=error)
            av=bilinear(alpha[frame],uv)
            check(item['label']+f' matte clips geometry frame{frame}',bool(np.all(av>=.01-1e-6)),minimum=float(av.min()))
            REPORT['samples'].append({'label':item['label'],'frame':frame,'vertices':len(co),'max_depth_error':error})
        # Raw toggle must sample untouched raw Z rather than the boundary extension.
        control['Use Clean Depth']=False;control.update_tag();s.frame_set(101);bpy.context.view_layer.update()
        mesh=surface.evaluated_get(bpy.context.evaluated_depsgraph_get()).data;co=np.empty((len(mesh.vertices),3),np.float32);uv=co.copy()
        mesh.vertices.foreach_get('co',co.reshape(-1));mesh.attributes[g.UV_NAME].data.foreach_get('vector',uv.reshape(-1))
        check(item['label']+' raw depth toggle',float(np.max(np.abs(-co[:,2]-bilinear(truth['raw'][0],uv))))<2e-5)
        control['Use Clean Depth']=True;control['Frame Index Offset']=2;control.update_tag();s.frame_set(101);bpy.context.view_layer.update()
        mesh=surface.evaluated_get(bpy.context.evaluated_depsgraph_get()).data;co=np.empty((len(mesh.vertices),3),np.float32);uv=co.copy()
        mesh.vertices.foreach_get('co',co.reshape(-1));mesh.attributes[g.UV_NAME].data.foreach_get('vector',uv.reshape(-1))
        check(item['label']+' offset selects only imported next frame',float(np.max(np.abs(-co[:,2]-bilinear(truth['clean'][1],uv))))<2e-5)
        control['Frame Index Offset']=1;control['Matte Threshold']=(10001/65535+39/255)/2;control.update_tag();s.frame_set(101);bpy.context.view_layer.update()
        mesh=surface.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        uv=np.empty((len(mesh.vertices),3),np.float32);mesh.attributes[g.UV_NAME].data.foreach_get('vector',uv.reshape(-1))
        inside=(uv[:,0]>10/128)&(uv[:,0]<29/128)&(uv[:,1]>1-12/72)&(uv[:,1]<1-6/72)
        check(item['label']+' precision-sensitive alpha plateau',bool(inside.any())==(item['alpha_dtype']=='uint8'),plateau_vertices=int(inside.sum()))
        control['Matte Threshold']=.01;control.update_tag()
    # Render in the task-owned scene only. Original preservation was checked above.
    for obj in s.objects:
        if not obj.get(g.ROLE):obj.hide_render=True
    s.render.engine='CYCLES';s.cycles.samples=8;s.cycles.device='CPU';s.render.resolution_x=256;s.render.resolution_y=144;s.render.resolution_percentage=100
    s.render.film_transparent=True;s.render.image_settings.file_format='PNG';s.render.image_settings.color_mode='RGBA';s.render.image_settings.color_depth='16'
    s.view_settings.view_transform='Standard';s.view_settings.look='None';s.view_settings.exposure=0;s.view_settings.gamma=1
    for item,col,objects,truth in imported:
        for _,other,_,_ in imported:other.hide_render=other!=col
        s.camera=objects['camera'];s.frame_set(103);bpy.context.view_layer.update()
        target=OUT/(item['label']+'_cycles.png');s.render.filepath=str(target);bpy.ops.render.render(write_still=True)
        check(item['label']+' Cycles render written',target.is_file() and target.stat().st_size>1000)
        image=bpy.data.images.load(str(target),check_existing=False);pixels=np.asarray(image.pixels[:]).reshape(144,256,4)
        check(item['label']+' render has transparent background and visible subject',pixels[:,:,3].min()==0 and pixels[:,:,3].max()>.95)
        bpy.data.images.remove(image);REPORT['renders'].append(str(target))
    for _,col,_,_ in imported:col.hide_render=False
    s.camera=camera;s.frame_set(101);s['custom_matte_qa_fixture_report']=str(args.fixture_report.resolve())
    check('final renderer is Cycles',s.render.engine=='CYCLES')
    path=OUT/'custom_matte_native.blend';bpy.ops.wm.save_as_mainfile(filepath=str(path))
    REPORT.update(status='passed',blend=str(path),fixture_report=str(args.fixture_report.resolve()))
    addon.unregister()


try:main()
except Exception:
    REPORT['status']='failed';REPORT['error']=traceback.format_exc();traceback.print_exc()
finally:
    (OUT/'blender_custom_matte_report.json').write_text(json.dumps(REPORT,indent=2))
    print('CUSTOM_MATTE_BLENDER '+json.dumps({'status':REPORT['status'],'checks':len(REPORT['checks'])}))
if REPORT['status']!='passed':raise RuntimeError('Custom matte native validation failed')

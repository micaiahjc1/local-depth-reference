"""Reopen native custom-matte scene without enabling the add-on or autoexec."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback
import bpy
import numpy as np

parser=argparse.ArgumentParser();parser.add_argument('--blend',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);out=args.output.resolve();report={'checks':[]}
def check(name,ok):
    report['checks'].append({'name':name,'passed':bool(ok)})
    if not ok:raise AssertionError(name)
try:
    check('background only',bpy.app.background)
    check('extensions isolated',Path(bpy.utils.user_resource('EXTENSIONS')).resolve().is_relative_to(out))
    check('addon is not enabled','local_depth_reference' not in bpy.context.preferences.addons)
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()),load_ui=False)
    s=bpy.context.scene;check('saved Cycles renderer',s.render.engine=='CYCLES')
    collections=[c for c in bpy.data.collections if c.name.startswith('QA ')]
    check('three imports persisted',len(collections)==3)
    for col in collections:
        controls=next(o for o in col.objects if o.get('ldr_role')=='controls');surface=next(o for o in col.objects if o.get('ldr_role')=='surface')
        for frame,empty in [(101,False),(104,True),(105,False)]:
            s.frame_set(frame);bpy.context.view_layer.update();mesh=surface.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
            check(col.name+f' native sequence frame{frame}',(len(mesh.vertices)==0) if empty else len(mesh.vertices)>100)
        check(col.name+' frame index default persists',controls['Frame Index Offset']==1)
    check('native drivers do not need autoexec',not bpy.app.autoexec_fail)
    report['status']='passed'
except Exception:
    report['status']='failed';report['error']=traceback.format_exc();traceback.print_exc()
finally:
    (out/'blender_custom_matte_reopen_report.json').write_text(json.dumps(report,indent=2));print(json.dumps({'status':report['status'],'checks':len(report['checks'])}))
if report['status']!='passed':raise RuntimeError('Reopen qualification failed')

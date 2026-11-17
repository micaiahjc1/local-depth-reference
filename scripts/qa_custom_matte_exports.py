"""Independent codec/cleanup/export qualification using explicit synthetic fixtures.

Model output is deterministic test data, not a new AI accuracy benchmark. Supplied
matte ingestion, cleanup, manifest validation and all AE exports are real code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'worker'))
sys.path.insert(0, str(ROOT/'local_depth_reference'))
from common import Context, atomic_json, digest, file_hash, sequence_files
from process import decode, export_depth
from shot_paths import load_package, load_ae_package, resolve_shot, reserve_run
import ae_export
import supplied_matte


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to((ROOT/'test-output').resolve()):
        raise ValueError('Use an owned test-output directory')
    out.mkdir(parents=True, exist_ok=False)
    shot=out/'Shot';shot.mkdir()
    binding=resolve_shot(shot)
    inputs=out/'input';inputs.mkdir()
    rgba_dir=inputs/'custom-rgba16';rgba_dir.mkdir()
    inverted_dir=inputs/'custom-inverted16';inverted_dir.mkdir()
    rgb_dir=inputs/'rgb';rgb_dir.mkdir()
    n,h,w=5,72,128
    yy,xx=np.mgrid[:h,:w]
    alpha=[];q=[]
    for i in range(n):
        signed=np.sqrt(((xx-(45+i*7))/30)**2+((yy-36)/25)**2)
        a=np.rint(np.clip((1.05-signed)/.15,0,1)*65535).astype(np.uint16)
        a[25:46,51:69]=65535
        # This plateau proves the pipeline has not reduced custom alpha to8bit.
        a[4:14,8:31]=10001+i*19
        if i==3:a[:]=0
        alpha.append(a)
        rgba=np.zeros((h,w,4),np.uint16)
        rgba[:,:,:3]=[50000,12000,4200]  # Deliberately not a grayscale matte.
        rgba[:,:,3]=a
        assert cv2.imwrite(str(rgba_dir/f'artist_{1001+i:05d}.png'),rgba)
        assert cv2.imwrite(str(inverted_dir/f'artist_{1001+i:05d}.tif'),65535-a)
        rgb=np.empty((h,w,3),np.uint8)
        rgb[:,:,0]=(xx*2+i*17)%256;rgb[:,:,1]=(yy*3+i*31)%256;rgb[:,:,2]=40+i*40
        assert cv2.imwrite(str(rgb_dir/f'{i:05d}.png'),rgb)
        z=np.asarray(1+.003*xx+.004*yy+i*.13,dtype=np.float32)
        z[a>=64250]=3+i*.21+.001*xx[a>=64250]
        q.append(z)
    alpha=np.stack(alpha);q=np.stack(q)
    source=inputs/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-n','-framerate','24000/1001','-i',str(rgb_dir/'%05d.png'),
                    '-frames:v',str(n),'-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
    input_hashes={str(p):file_hash(p) for p in inputs.rglob('*') if p.is_file()}
    report={'status':'running','fixture_kind':'deterministic depth/model-stage fixture; no AI quality claim',
            'checks':[],'packages':[],'width':w,'height':h,'frames':n,'fps_num':24000,'fps_den':1001}
    def check(name,value,**extra):
        report['checks'].append({'name':name,'passed':bool(value),**extra})
        if not value:raise AssertionError(name)
    configs=[('automatic',{'matte_source':'automatic'},np.rint(alpha/257).astype(np.uint8)),
             ('custom_alpha',{'matte_source':'custom','matte_path':str(rgba_dir/'artist_01001.png'),'matte_channel':'alpha','matte_invert':False},alpha),
             ('custom_luma_inverted',{'matte_source':'custom','matte_path':str(inverted_dir/'artist_01001.tif'),'matte_channel':'luma','matte_invert':True},alpha)]
    try:
        for label,mode,expected_alpha in configs:
            run=reserve_run(binding)
            job={'schema':1,'source':str(source),'shot_dir':str(shot),'run_dir':str(run),'binding':binding,
                 'runtime':{},'settings':{'working_max_width':128,'edge_radius':2,'edge_inset':0,'cleanup_strength':1,**mode}}
            ctx=Context(job);atomic_json(run/'job.json',ctx.job)
            rows={'decode':decode(ctx,label+'-decode')}
            if mode['matte_source']=='custom':
                inspected=supplied_matte.inspect_sequence(ctx,rows['decode']['data'])
                rows['matte']=supplied_matte.import_sequence(ctx,digest(inspected),inspected)
                supplied_matte.verify_sequence(ctx,inspected)
            else:
                folder=run/'matte';folder.mkdir()
                for f in range(n):assert cv2.imwrite(str(folder/f'{f:05d}.png'),expected_alpha[f])
                rows['matte']=ctx.finish_stage('matte','automatic-stage-fixture',sequence_files(folder,n,'.png'),
                    {'test_fixture':True,'description':'Deterministic8bit automatic-stage output stand-in; no inference invoked'})
            (run/'depth').mkdir(exist_ok=True)
            np.save(run/'depth/relative_raw.npy',q)
            rows['depth']=ctx.finish_stage('depth','depth-stage-fixture',[run/'depth/relative_raw.npy'],
                {'test_fixture':True,'description':'Deterministic inverse depth'})
            rows['export']=export_depth(ctx,label+'-export')
            check(label+' cleanup leaves confident core unchanged',rows['export']['data']['max_core_change']==0)
            check(label+' cleanup actually changes boundary',rows['export']['data']['boundary_pixels_blended']>0)
            raw=[];clean=[]
            for f in range(n):
                got=cv2.imread(str(run/'alpha'/f'{f:05d}.png'),cv2.IMREAD_UNCHANGED)
                check(label+f' base alpha exact frame{f}',np.array_equal(got,expected_alpha[f]),dtype=str(got.dtype))
                raw.append(ae_export.read_z(run/'depth/raw_z'/f'{f:05d}.exr',w,h))
                clean.append(ae_export.read_z(run/'depth/clean_z'/f'{f:05d}.exr',w,h))
            src={k:v for k,v in rows['decode']['data'].items() if k not in ('proxy','working_width','working_height','timestamps_seconds')}
            manifest={'schema':1,'type':'local-depth-reference','status':'complete','source':src,
                'processing':{'working_width':w,'working_height':h,'q_median':rows['export']['data']['q_median'],
                              'matte_source':mode['matte_source'],'settings':ctx.settings,'matte_inference':rows['matte']['data'],
                              'normalization':rows['export']['data'],'qa_fixture':True},
                'paths':{'source':'source/00000.png','alpha':'alpha/00000.png','raw_depth':'depth/raw_z/00000.exr','clean_depth':'depth/clean_z/00000.exr'},
                'binding':binding,'sequence':{'first_index':0,'count':n,'digits':5},
                'stage_cache':{k:{'key':v['key'],'artifacts':v['artifacts']} for k,v in rows.items()}}
            atomic_json(run/'manifest.json',manifest)
            package=load_package(run/'manifest.json');check(label+' complete package validates',package['frame_count']==n)
            np.savez_compressed(out/(label+'_truth.npz'),alpha=expected_alpha,raw=np.stack(raw),clean=np.stack(clean))
            item={'label':label,'manifest':str(run/'manifest.json'),'truth':str(out/(label+'_truth.npz')),
                  'alpha_dtype':str(expected_alpha.dtype),'ae':{}}
            for coverage in ('whole_scene','person','both'):
                ae_run=reserve_run(binding)
                ae_job={'schema':1,'kind':'ae_export','source_manifest':str(run/'manifest.json'),
                    'source_manifest_sha256':file_hash(run/'manifest.json'),'run_dir':str(ae_run),'shot_dir':str(shot),
                    'binding':binding,'runtime':{},'settings':{'ae_coverage':coverage}}
                result=ae_export.export_job(ae_job)
                validated=load_ae_package(ae_run/'manifest.json')
                check(label+' '+coverage+' export validates',validated['coverage']==coverage)
                check(label+' '+coverage+' rational timing exact',(validated['fps_num'],validated['fps_den'],validated['frame_count'])==(24000,1001,n))
                check(label+' '+coverage+' stream scope',set(result['streams'])==({'whole_scene','person'} if coverage=='both' else {coverage}))
                for stream in result['streams']:
                    near=result['streams'][stream]['near'];far=result['streams'][stream]['far']
                    values=raw if stream=='whole_scene' else clean
                    for f in range(n):
                        expected=np.rint(np.clip((values[f].astype(np.float64)-near)/(far-near),0,1)*65535).astype(np.uint16)
                        observed=cv2.imread(str(ae_run/'ae'/stream/f'{f:05d}.png'),cv2.IMREAD_UNCHANGED)
                        check(label+f' {coverage}/{stream} actual depth frame{f}',np.array_equal(observed,expected))
                if coverage in ('person','both'):
                    for f in range(n):
                        got=cv2.imread(str(ae_run/'ae/alpha'/f'{f:05d}.png'),cv2.IMREAD_UNCHANGED)
                        expected=expected_alpha[f].astype(np.uint16)*(257 if expected_alpha.dtype==np.uint8 else 1)
                        check(label+f' {coverage} alpha precision frame{f}',np.array_equal(got,expected))
                    check(label+' '+coverage+' empty frame retained',not cv2.imread(str(ae_run/'ae/alpha/00003.png'),cv2.IMREAD_UNCHANGED).any())
                item['ae'][coverage]=str(ae_run/'manifest.json')
            report['packages'].append(item)
        a,b=report['packages'][1:]
        for coverage in ('whole_scene','person','both'):
            ma=json.loads(Path(a['ae'][coverage]).read_text());mb=json.loads(Path(b['ae'][coverage]).read_text())
            for stream in ma['streams']:
                for f in range(n):
                    pa=Path(a['ae'][coverage]).parent/'ae'/stream/f'{f:05d}.png'
                    pb=Path(b['ae'][coverage]).parent/'ae'/stream/f'{f:05d}.png'
                    check(f'custom alpha and inverted luma equivalent {coverage}/{stream}/{f}',np.array_equal(cv2.imread(str(pa),-1),cv2.imread(str(pb),-1)))
        for path,sha in input_hashes.items():check('input unchanged '+Path(path).name,file_hash(path)==sha)
        report['status']='passed'
    except Exception as error:
        report['status']='failed';report['error']=str(error)
        raise
    finally:
        atomic_json(out/'fixture_report.json',report)
        print(json.dumps({'status':report['status'],'checks':len(report['checks']),'report':str(out/'fixture_report.json')}))


if __name__=='__main__':main()

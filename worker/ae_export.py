"""Export AE depth mattes from a completed Blender package; no AI/model work."""
import argparse
import copy
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import traceback
import uuid

from common import Context,Cancelled,acquire_lock,atomic_json,file_hash,read_json,validate_job
from supplied_matte import read_coverage,coverage_scale

COVERAGES={'whole_scene','person','both'}
CORE_RADIUS=2
CHUNK_VALUES=1024*1024

def shot_paths_module():
    here=Path(__file__).resolve().parent
    for directory in [here.parent,here.parent/'local_depth_reference']:
        if (directory/'shot_paths.py').is_file():
            sys.path.insert(0,str(directory));return importlib.import_module('shot_paths')
    raise ImportError('Bundled shot_paths.py was not found; reinstall Local Depth Reference')

def validate_ae_job(job):
    if not isinstance(job,dict) or job.get('schema',1)!=1 or job.get('kind')!='ae_export':
        raise ValueError('Expected schema1 job with kind ae_export')
    if not isinstance(job.get('source_manifest'),str) or not Path(job['source_manifest']).is_absolute():
        raise ValueError('source_manifest must be an absolute path')
    source=Path(job['source_manifest']).expanduser().resolve(strict=True)
    if not source.is_file():raise ValueError('source_manifest must be a file')
    settings=job.get('settings',{})
    if not isinstance(settings,dict) or settings.get('ae_coverage') not in COVERAGES:
        raise ValueError('settings.ae_coverage must be whole_scene, person, or both')
    # Reuse physical/UUID validation without requiring the original video. The
    # common validator only needs this existing file for its source-path guard.
    normalized=validate_job({**job,'source':str(source),'runtime':job.get('runtime',{})})
    run=Path(normalized['run_dir'])
    if source.is_relative_to(run):raise ValueError('Destination must not contain or overwrite the source package')
    pinned=job.get('source_manifest_sha256',job.get('manifest_sha256'))
    if pinned is not None and (not isinstance(pinned,str) or not re.fullmatch(r'[a-fA-F0-9]{64}',pinned)):
        raise ValueError('source_manifest_sha256 must contain 64 hexadecimal digits')
    normalized['source_manifest']=str(source);normalized['source_manifest_sha256']=pinned.lower() if pinned else None
    return normalized

def safe_child(ctx,relative):
    path=(ctx.run/relative).resolve()
    if not path.is_relative_to(ctx.run) or path==ctx.run:raise ValueError('Export path escapes the owned version')
    return path

def update(ctx,stage,fraction,message,**extra):
    ctx.cancel();ctx.progress(stage,fraction,message,job_kind='ae_export',**extra)

def validate_source_package(ctx,source,stage='ae_validate_source',fraction=.05):
    module=shot_paths_module();original=module.sha256_file;calls=0
    def checked_hash(path):
        nonlocal calls
        ctx.cancel();result=file_hash(path,ctx.cancel);calls+=1
        if calls%16==0:update(ctx,stage,fraction,'Verifying source package integrity',files_checked=calls)
        return result
    try:
        module.sha256_file=checked_hash
        return module.load_package(source)
    finally:module.sha256_file=original

def check_same_shot(package,binding):
    original=package['binding']
    if any(original.get(k) for k in ['shot_id','project_id']):
        for field in ['shot_id','project_id']:
            try:actual=uuid.UUID(original.get(field,''));target=uuid.UUID(binding.get(field,''))
            except (ValueError,TypeError,AttributeError) as exc:raise ValueError('Source and destination require valid matching '+field) from exc
            if actual!=target:raise ValueError('Source package '+field+' differs from the destination binding')
    elif not original.get('shot_dir') or Path(original['shot_dir']).resolve()!=Path(binding['shot_dir']).resolve():
        raise ValueError('Source package belongs to a different shot folder')

def frame_path(package,name,index):
    first=Path(package['streams'][name]);digits=package['metadata']['sequence']['digits']
    return first.parent/f'{index:0{digits}d}{first.suffix}'

def read_z(path,width,height):
    import numpy as np
    import OpenEXR
    with OpenEXR.File(str(path)) as file:
        channels=file.channels()
        if 'RGB' in channels:
            rgb=channels['RGB'].pixels
            if rgb.shape!=(height,width,3) or rgb.dtype!=np.float32:raise ValueError('Depth must be float32 RGB with declared dimensions: '+str(path))
            if not np.array_equal(rgb[:,:,0],rgb[:,:,1]) or not np.array_equal(rgb[:,:,0],rgb[:,:,2]):raise ValueError('Depth RGB channels disagree: '+str(path))
            z=rgb[:,:,0].copy()
        elif 'R' in channels:
            z=channels['R'].pixels.copy()
            for name in ['G','B']:
                if name in channels and not np.array_equal(z,channels[name].pixels):raise ValueError('Depth channels disagree: '+str(path))
        else:raise ValueError('Depth EXR has no R/RGB channel: '+str(path))
    if z.shape!=(height,width) or z.dtype!=np.float32 or not np.isfinite(z).all() or np.any(z<=0):
        raise ValueError('Depth must be finite positive float32 with declared dimensions: '+str(path))
    return z

def read_alpha(path,width,height):
    return read_coverage(path,width,height)

def exact_positive_f32_percentiles(path,count,percentiles,cancel=lambda:None,progress=lambda value:None):
    """Exact linear percentiles using four streaming radix passes, bounded RAM.

    Positive IEEE float32 bit patterns sort in numeric order. Only histograms,
    four selected ranks and one 4MiB chunk are retained, never a whole clip.
    """
    import numpy as np
    if count<1 or Path(path).stat().st_size!=count*4:raise ValueError('Invalid percentile scratch length')
    positions=[(count-1)*float(p)/100 for p in percentiles]
    if any(not math.isfinite(p) or p<0 or p>count-1 for p in positions):raise ValueError('Invalid percentile')
    orders=sorted(set([math.floor(p) for p in positions]+[math.ceil(p) for p in positions]))
    states={rank:(0,rank) for rank in orders}
    for pass_index,shift in enumerate([24,16,8,0]):
        hist={prefix:np.zeros(256,np.int64) for prefix,_ in states.values()};seen=0
        with Path(path).open('rb') as stream:
            while True:
                cancel();block=stream.read(CHUNK_VALUES*4)
                if not block:break
                bits=np.frombuffer(block,dtype=np.float32).view(np.uint32);seen+=len(bits)
                for prefix,bins in hist.items():
                    values=bits if pass_index==0 else bits[(bits>>(shift+8))==prefix]
                    bins+=np.bincount(((values>>shift)&255).astype(np.int32),minlength=256)
                progress((pass_index+seen/count)/4)
        if seen!=count:raise ValueError('Percentile scratch changed during read')
        for rank,(prefix,remainder) in list(states.items()):
            cumulative=np.cumsum(hist[prefix]);bucket=int(np.searchsorted(cumulative,remainder,side='right'))
            if bucket>255:raise ValueError('Invalid depth percentile rank')
            before=int(cumulative[bucket-1]) if bucket else 0
            states[rank]=((prefix<<8)|bucket,remainder-before)
    values={rank:float(np.array([prefix],np.uint32).view(np.float32)[0]) for rank,(prefix,_) in states.items()}
    return [values[math.floor(p)]*(1-(p-math.floor(p)))+values[math.ceil(p)]*(p-math.floor(p)) for p in positions]

def fixed_range(near,far,basis):
    if not(math.isfinite(near) and math.isfinite(far) and near>0 and far>=near):raise ValueError('Invalid depth range')
    result={'near':float(near),'far':float(far),'range_basis':basis,'per_frame_normalization':False,'constant_depth':near==far}
    if near==far:
        epsilon=max(abs(near)*1e-6,1e-12)
        result.update(near=max(near-epsilon,near*.5),far=far+epsilon,constant_depth_value=float(near))
    return result

def normalize_u16(z,limits):
    import numpy as np
    return np.round(np.clip((z.astype(np.float64)-limits['near'])/(limits['far']-limits['near']),0,1)*65535).astype(np.uint16)

def write_u16(ctx,path,array):
    import cv2
    import numpy as np
    if array.dtype!=np.uint16 or array.ndim!=2:raise ValueError('Expected grayscale uint16 output')
    path=safe_child(ctx,path.relative_to(ctx.run));path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.stem+'.'+uuid.uuid4().hex+'.tmp.png')
    try:
        ctx.cancel()
        if not cv2.imwrite(str(temporary),array,[cv2.IMWRITE_PNG_COMPRESSION,3]):raise IOError('PNG write failed')
        decoded=cv2.imread(str(temporary),cv2.IMREAD_UNCHANGED)
        if decoded.dtype!=np.uint16 or not np.array_equal(decoded,array):raise ValueError('16-bit PNG readback mismatch')
        for attempt in range(10):
            try:os.replace(temporary,path);break
            except PermissionError:
                if attempt==9:raise
                time.sleep(.05)
        return {'path':ctx.relative(path),'bytes':path.stat().st_size,'sha256':file_hash(path,ctx.cancel)}
    finally:temporary.unlink(missing_ok=True)

def clipping(z,limits,mask=None):
    values=z if mask is None else z[mask]
    return {'pixels':int(values.size),'below_near':int((values<limits['near']).sum()),'above_far':int((values>limits['far']).sum())}

def write_import_guide(ctx,package,ranges,alpha_bits=8):
    source=package['metadata']['source'];fps_num=source['fps_num'];fps_den=source['fps_den'];digits=package['metadata']['sequence']['digits']
    lines=['After Effects depth export','',
        f'Import the first PNG of each requested stream as an image sequence: {0:0{digits}d}.png.',
        f'Conform the sequence to the exact source frame rate: {fps_num}/{fps_den} fps ({fps_num/fps_den:.12f} fps).',
        f'Native map size: {package["working_width"]} x {package["working_height"]}; frames: {package["frame_count"]}.',
        'Use a 16- or 32-bpc AE project. These are 16-bit grayscale PNG data maps, not a display-grade image.',
        'Preserve RGB/numeric data interpretation; do not apply a display-gamma bake or unintended color-profile conversion.',
        'Black is near; white is far. Each range below is fixed for the entire shot.','']
    for name,limits in ranges.items():
        lines.extend([f'{name}: ae/{name}/{0:0{digits}d}.png',f'Fixed relative-Z near/far: {limits["near"]:.12g} / {limits["far"]:.12g}.'])
    if 'person' in ranges:
        lines.extend(['',f'Person alpha: ae/alpha/{0:0{digits}d}.png.',
            'Person depth RGB is unpremultiplied. Apply this separate alpha as a matte once; do not multiply by alpha and then apply it again.',
            'The source 16-bit alpha is retained exactly.' if alpha_bits==16 else
            'The original 8-bit alpha was encoded as 16-bit by multiplying values by 257. This adds no new alpha precision.',
            'Person uses the existing Blender boundary-cleaned depth. Its background values are not a whole-scene depth map.'])
    lines.extend(['','The first filename is zero-based and matches source decoded frame 0. Do not add a one-frame content shift.',
        'No inference, spatial smoothing, neural upscale, retiming, scene change or rig operation was performed.',
        'Relative Z is not measured distance in meters. The original float depth remains in the source Blender package.',''])
    path=safe_child(ctx,'AE_IMPORT_GUIDE.txt');temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text('\n'.join(lines),encoding='utf-8')
        for attempt in range(10):
            try:os.replace(temp,path);break
            except PermissionError:
                if attempt==9:raise
                time.sleep(.05)
    finally:temp.unlink(missing_ok=True)
    return {'path':ctx.relative(path),'bytes':path.stat().st_size,'sha256':file_hash(path,ctx.cancel)}

def export_job(job,progress_hook=None):
    """Run one export; hook is for integration tests/observers, not native apps."""
    import cv2
    import numpy as np
    normalized=validate_ae_job(job);run=Path(normalized['run_dir'])
    if (run/'manifest.json').exists():raise ValueError('Destination already has a manifest; create a new version')
    ctx=Context(normalized)
    if not ctx.cache.resolve().is_relative_to(ctx.run):raise ValueError('Worker cache escapes the owned version')
    lock=None;scratch=None;owns=False;last_stage='ae_validate_source';last_fraction=0
    def emit(stage,fraction,message,**extra):
        nonlocal last_stage,last_fraction
        last_stage=stage;last_fraction=fraction
        update(ctx,stage,fraction,message,**extra)
        if progress_hook:progress_hook(stage,fraction,ctx)
        ctx.cancel()
    try:
        lock=acquire_lock(ctx);owns=True;start=time.perf_counter();cv2.setNumThreads(4)
        emit('ae_validate_source',0,'Validating completed source package')
        source=Path(normalized['source_manifest']);source_sha=file_hash(source,ctx.cancel)
        pinned=normalized.get('source_manifest_sha256')
        if pinned and pinned!=source_sha:raise ValueError('Source manifest hash differs from the submitted job')
        package=validate_source_package(ctx,source);check_same_shot(package,normalized['binding'])
        if file_hash(source,ctx.cancel)!=source_sha:raise ValueError('Source manifest changed during validation')
        n=package['frame_count'];w=package['working_width'];h=package['working_height'];digits=package['metadata']['sequence']['digits']
        coverage=normalized['settings']['ae_coverage'];whole=coverage in ['whole_scene','both'];person=coverage in ['person','both']
        output=safe_child(ctx,'ae')
        if output.exists():shutil.rmtree(output)
        output.mkdir()
        if whole:
            if shutil.disk_usage(ctx.cache).free<n*w*h*4+64*1024*1024:raise OSError('Insufficient free disk space for bounded-memory depth range calculation')
            fd,name=tempfile.mkstemp(prefix='ae_depth_',suffix='.f32',dir=ctx.cache);os.close(fd);scratch=Path(name)
        envelope=[];empty_core_frames=[];kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*CORE_RADIUS+1,)*2)
        alpha_bits=None;alpha_scale=1
        scratch_stream=scratch.open('wb') if scratch else None
        try:
            for i in range(n):
                ctx.cancel();raw=read_z(frame_path(package,'raw_depth',i),w,h)
                if scratch_stream:scratch_stream.write(raw.tobytes(order='C'))
                if person:
                    alpha=read_alpha(frame_path(package,'alpha',i),w,h);bits=alpha.dtype.itemsize*8
                    if alpha_bits is not None and bits!=alpha_bits:raise ValueError('Source alpha precision changes across frames')
                    alpha_bits=bits;alpha_scale=coverage_scale(alpha)
                    core=cv2.erode((alpha>=250*alpha_scale).astype(np.uint8),kernel)>0
                    read_z(frame_path(package,'clean_depth',i),w,h)
                    if core.any():envelope.append((i,*np.percentile(raw[core],[.5,99.5]).tolist()))
                    else:empty_core_frames.append(i)
                emit('ae_validate_pixels',.10+.20*(i+1)/n,f'Validating depth pixels {i+1}/{n}',frame=i+1,frame_count=n)
        finally:
            if scratch_stream:scratch_stream.close()
        ranges={}
        if whole:
            values=exact_positive_f32_percentiles(scratch,n*w*h,[.5,99.5],ctx.cancel,
                lambda part:emit('ae_range',.30+.25*part,'Computing fixed whole-scene range'))
            ranges['whole_scene']=fixed_range(*values,'Exact whole-shot raw-depth 0.5th and99.5th percentiles; all pixels/all frames')
        if person:
            if not envelope:raise ValueError('Person export has no confident subject core in any frame')
            lower=min(envelope,key=lambda row:row[1]);upper=max(envelope,key=lambda row:row[2])
            ranges['person']=fixed_range(lower[1],upper[2],'Fixed envelope: minimum per-frame RAW core0.5th percentile to maximum per-frame RAW core99.5th percentile')
            ranges['person'].update(core_alpha_threshold=250*alpha_scale,core_alpha_threshold_normalized=250/255,core_erosion_radius=CORE_RADIUS,
                near_endpoint_source_index=lower[0],far_endpoint_source_index=upper[0],empty_core_source_indices=empty_core_frames)
        if scratch:scratch.unlink();scratch=None
        inventory=[];frame_stats=[];totals={name:{'pixels':0,'below_near':0,'above_far':0} for name in ranges}
        for i in range(n):
            ctx.cancel();name=f'{i:0{digits}d}.png';row={'source_index':i,'streams':{}}
            if whole:
                z=read_z(frame_path(package,'raw_depth',i),w,h)
                inventory.append(write_u16(ctx,output/'whole_scene'/name,normalize_u16(z,ranges['whole_scene'])))
                row['streams']['whole_scene']=clipping(z,ranges['whole_scene'])
            if person:
                z=read_z(frame_path(package,'clean_depth',i),w,h);alpha=read_alpha(frame_path(package,'alpha',i),w,h)
                inventory.append(write_u16(ctx,output/'person'/name,normalize_u16(z,ranges['person'])))
                inventory.append(write_u16(ctx,output/'alpha'/name,alpha.astype(np.uint16)*257 if alpha.dtype==np.uint8 else alpha))
                row['streams']['person']=clipping(z,ranges['person'],alpha>=128*coverage_scale(alpha))
            for stream,values in row['streams'].items():
                for key,value in values.items():totals[stream][key]+=value
            frame_stats.append(row)
            emit('ae_export',.55+.38*(i+1)/n,f'Writing AE frame {i+1}/{n}',frame=i+1,frame_count=n)
        for stream,values in totals.items():
            for key in ['below_near','above_far']:values[key+'_percent']=100*values[key]/values['pixels'] if values['pixels'] else 0.0
        inventory.append(write_import_guide(ctx,package,ranges,alpha_bits or 8))
        for i,item in enumerate(inventory):
            ctx.cancel();path=safe_child(ctx,item['path'])
            if path.stat().st_size!=item['bytes'] or file_hash(path,ctx.cancel)!=item['sha256']:raise ValueError('Export file changed during final validation')
            if i%16==0:emit('ae_validate_output',.93+.06*(i+1)/len(inventory),'Verifying completed AE streams')
        validate_ae_job(job);check_same_shot(package,ctx.job['binding'])
        if file_hash(source,ctx.cancel)!=source_sha:raise ValueError('Source manifest changed during export')
        # Recheck every source-package frame checksum after consuming the data;
        # manifest hashing alone would not catch a changed EXR/PNG payload.
        final_package=validate_source_package(ctx,source,stage='ae_revalidate_source',fraction=.995)
        check_same_shot(final_package,ctx.job['binding'])
        if file_hash(source,ctx.cancel)!=source_sha:raise ValueError('Source manifest changed during final verification')
        streams={key:{'path':f'ae/{key}/{0:0{digits}d}.png',**value,'source_field':'raw_depth' if key=='whole_scene' else 'clean_depth'} for key,value in ranges.items()}
        if person:streams['person'].update(alpha_path=f'ae/alpha/{0:0{digits}d}.png',premultiplied=False)
        manifest={'schema':1,'type':'local-depth-ae-export','status':'complete','coverage':coverage,
            'source':copy.deepcopy(package['metadata']['source']),'sequence':copy.deepcopy(package['metadata']['sequence']),
            'processing':{'working_width':w,'working_height':h,'q_median':package['metadata']['processing'].get('q_median'),
                'matte_source':package['metadata']['processing'].get('matte_source','automatic'),
                'alpha_bit_depth':alpha_bits,
                'new_model_inference':False,'spatial_filtering':False,'existing_blender_cleanup_preserved':True,
                'per_frame_normalization':False,'seconds_export':time.perf_counter()-start,
                'range_memory_strategy':'Frame-bounded buffers and4MiB streaming radix chunks; no whole-clip RAM allocation; scratch only under owned run'},
            'binding':ctx.job['binding'],'source_package':{'manifest_path':str(source),'manifest_sha256':source_sha,
                'original_type':'local-depth-reference','original_video_required':False},
            'streams':streams,'encoding':{'format':'PNG','dtype':'uint16','channels':1,'polarity':'0 near black /65535 far white',
                'formula':'round(clamp((Z-near)/(far-near),0,1)*65535)','person_depth_premultiplied':False,
                'alpha_conversion':None if not person else 'Source16bit alpha retained exactly' if alpha_bits==16 else 'Source8bit alpha multiplied by257; no added precision','data_not_display_color':True},
            'inventory':inventory,'clipping':{'whole_scene_domain':'all pixels','person_domain':'source alpha>='+str(128*alpha_scale),
                'totals':totals,'frames':frame_stats},
            'validation':{'source_package_load_package_passed':True,'source_manifest_hash_unchanged':True,
                'source_frame_hashes_revalidated_after_export':True,
                'all_requested_depth_pixels_positive_finite_float32':True,'all_pngs_exact_uint16_readback':True,
                'all_export_inventory_hashes_verified':True,'source_frame_order_and_rational_fps_preserved':True},
            'exporter_sha256':file_hash(__file__),
            'import_guide':'AE_IMPORT_GUIDE.txt',
            'notes':['Whole Scene uses original raw Z, never subject boundary-extended background.',
                     'Person Only uses the existing cleaned Z unchanged before fixed normalization; apply separate alpha in AE.',
                     'Depth is relative, not measured meters. No model inference, upscale, smoothing or rig operation occurs.']}
        ctx.cancel();validate_ae_job(job)
        if (ctx.run/'manifest.json').exists():raise ValueError('Destination manifest appeared during export; refusing overwrite')
        atomic_json(ctx.run/'manifest.json',manifest)
        ctx.progress('ae_complete',1,'AE depth export complete',status='complete',job_kind='ae_export',manifest='manifest.json')
        return manifest
    except Cancelled:
        if owns:ctx.progress(last_stage,last_fraction,'AE export cancelled',status='cancelled',job_kind='ae_export')
        raise
    except Exception as exc:
        if owns:ctx.progress(last_stage,last_fraction,str(exc),status='error',job_kind='ae_export',error_type=type(exc).__name__)
        raise
    finally:
        if scratch:scratch.unlink(missing_ok=True)
        if lock:
            try:
                if read_json(lock).get('pid')==os.getpid():lock.unlink()
            except OSError:pass

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--job',required=True,type=Path);args=parser.parse_args()
    try:
        result=export_job(read_json(args.job))
        print(json.dumps({'status':'complete','coverage':result['coverage'],'manifest':str(Path(read_json(args.job)['run_dir'])/'manifest.json')}),flush=True)
        return 0
    except Cancelled:return 21
    except Exception:
        traceback.print_exc();return 1

if __name__=='__main__':sys.exit(main())

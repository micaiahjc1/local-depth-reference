"""External local processing worker. No Blender, Beeble or pose dependencies."""
import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from common import (Context,Cancelled,AwaitingSubject,atomic_json,read_json,file_hash,
                    digest,code_hash,tree_hash,sequence_files,validate_job,acquire_lock)
from supplied_matte import read_coverage,coverage_scale

def clear_owned(ctx,folder):
    folder=Path(folder).resolve()
    if not folder.is_relative_to(ctx.run) or folder==ctx.run:
        raise ValueError('Refusing to clear outside a worker stage')
    if folder.exists():shutil.rmtree(folder)
    folder.mkdir(parents=True)

def run_child(ctx,stage,key):
    python=ctx.runtime['matting_python'] if stage in ['tracking','matte'] else ctx.runtime['vda_python']
    jobpath=ctx.cache/'effective_job.json';atomic_json(jobpath,ctx.job)
    logs=ctx.run/'logs';logs.mkdir(exist_ok=True)
    with (logs/(stage+'.log')).open('w',encoding='utf-8') as log:
        child=subprocess.Popen([str(python),str(Path(__file__).resolve()),'--job',str(jobpath),
                                '--internal-stage',stage,'--key',key],stdout=log,stderr=subprocess.STDOUT)
        while child.poll() is None:time.sleep(.15)
    if child.returncode==21:raise Cancelled('Cancelled during '+stage)
    if child.returncode:
        tail=(logs/(stage+'.log')).read_text(encoding='utf-8',errors='replace')[-5000:]
        raise RuntimeError(f'{stage} failed (exit {child.returncode}): {tail}')
    row=ctx.cache_record(stage,key)
    if row is None:raise RuntimeError(f'{stage} returned without a valid stage cache')
    return row

def decode(ctx,key):
    import cv2
    cv2.setNumThreads(4)
    ctx.progress('decode',0,'Inspecting and decoding original source')
    probe=json.loads(subprocess.check_output([ctx.runtime.get('ffprobe','ffprobe'),'-v','error',
        '-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,sample_aspect_ratio,duration:stream_side_data=rotation',
        '-of','json',ctx.job['source']],text=True))
    streams=probe.get('streams',[])
    if not streams:raise ValueError('Source contains no video stream')
    stream=streams[0];rate=Fraction(stream.get('avg_frame_rate','0/1'))
    if rate<=0:rate=Fraction(stream.get('r_frame_rate','0/1'))
    if rate<=0:raise ValueError('Source has no valid rational frame rate')
    rotation=float(next((x['rotation'] for x in stream.get('side_data_list',[]) if 'rotation' in x),0))
    # Make orientation behavior explicit: OpenCV versions differ for rotation
    # metadata. Reject it instead of silently mismatching RGB, matte and depth.
    if abs(rotation)%360>.001:raise ValueError('Rotated video metadata is not supported in v1. Bake rotation into pixels before processing.')
    sar=stream.get('sample_aspect_ratio','1:1')
    sar='1/1' if sar in ['N/A','0:1','0/1'] else sar.replace(':','/')
    par=Fraction(sar)
    if par!=1:raise ValueError('Non-square-pixel source is not supported in v1. Convert to square pixels without changing frame timing.')
    # Preserve decoded frame count and explicit rational cadence. VFR cannot be
    # represented by a simple Blender image sequence and is rejected clearly.
    ts=json.loads(subprocess.check_output([ctx.runtime.get('ffprobe','ffprobe'),'-v','error',
        '-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',ctx.job['source']],text=True))
    times=[float(x['best_effort_timestamp_time']) for x in ts.get('frames',[]) if 'best_effort_timestamp_time' in x]
    if len(times)>2:
        expected=1/float(rate)
        if any(abs((b-a)-expected)>max(.0001,expected*.01) for a,b in zip(times,times[1:])):
            raise ValueError('Variable-frame-rate footage is not supported in v1. Convert to constant frame rate first.')
    folder=ctx.run/'source';clear_owned(ctx,folder)
    cap=cv2.VideoCapture(ctx.job['source'])
    if not cap.isOpened():raise ValueError('OpenCV could not open source')
    count_hint=max(1,int(cap.get(cv2.CAP_PROP_FRAME_COUNT)));count=0;size=None
    try:
        while True:
            ctx.cancel();ok,bgr=cap.read()
            if not ok:break
            h,w=bgr.shape[:2]
            if size is None:
                size=(w,h)
                if size!=(stream['width'],stream['height']):raise ValueError('Decoded dimensions differ from stream metadata; orientation mismatch')
                scale=min(1,ctx.settings['working_max_width']/w)
                ww=max(2,int(round(w*scale)));wh=max(2,int(round(h*scale)))
                # H.264 tracking proxy needs even dimensions. Preserve proportional
                # scaling to within one pixel; use actual work dimensions everywhere.
                ww=ww//2*2;wh=wh//2*2
            if size!=(w,h):raise ValueError('Changing source dimensions are not supported')
            resized=cv2.resize(bgr,(ww,wh),interpolation=cv2.INTER_AREA)
            if not cv2.imwrite(str(folder/f'{count:05d}.png'),resized,[cv2.IMWRITE_PNG_COMPRESSION,2]):raise IOError('PNG write failed')
            count+=1
            if count%8==0:ctx.progress('decode',min(.75,.75*count/count_hint),f'Decoded {count} frames',frame=count)
    finally:cap.release()
    if count<1:raise ValueError('Source has no decoded frames')
    if len(times) and len(times)!=count:raise ValueError('Timestamp count and decoded frame count disagree')
    proxy=ctx.cache/'tracking_proxy.mp4';proxy.unlink(missing_ok=True)
    command=[ctx.runtime.get('ffmpeg','ffmpeg'),'-v','error','-n','-framerate',str(rate),'-start_number','0',
             '-i',str(folder/'%05d.png'),'-frames:v',str(count),'-c:v','libx264','-crf','12','-preset','fast',
             '-pix_fmt','yuv420p','-fps_mode','passthrough',str(proxy)]
    ctx.cancel();subprocess.run(command,check=True);ctx.cancel()
    check=cv2.VideoCapture(str(proxy));proxy_count=0
    while True:
        ok,frame=check.read()
        if not ok:break
        if frame.shape[:2]!=(wh,ww):raise ValueError('Tracking proxy dimensions mismatch')
        proxy_count+=1
    check.release()
    if proxy_count!=count:raise ValueError('Tracking proxy changed the frame count')
    data={'path':ctx.job['source'],'sha256':file_hash(ctx.job['source'],ctx.cancel),
          'width':size[0],'height':size[1],'encoded_width':stream['width'],'encoded_height':stream['height'],
          'rotation_degrees':rotation,'fps_num':rate.numerator,'fps_den':rate.denominator,'fps':str(rate),
          'frame_count':count,'pixel_aspect':1,'working_width':ww,'working_height':wh,
          'proxy':ctx.relative(proxy),'frame_mapping':'output index i = source decoded index i; no offset, skips or duplicates',
          'timestamps_seconds':times}
    return ctx.finish_stage('decode',key,[*sequence_files(folder,count,'.png'),proxy],data)

def choose_subject(ctx,decode_row,track_row):
    import cv2
    import numpy as np
    tracks=track_row['data'];source=decode_row['data'];n=source['frame_count']
    ww,wh=source['working_width'],source['working_height']
    rows=tracks['candidates'];credible=[r for r in rows if r['credible']]
    sid=ctx.settings['subject_id']
    if ctx.settings['subject_points']:sid=0
    if sid is not None:
        selected=next((r for r in rows if r['id']==sid and r['nonempty_frames']>0),None)
        if selected is None:raise ValueError(f'Selected subject ID {sid} is absent or empty')
    elif len(credible)==1:selected=credible[0]
    else:
        display=credible if credible else rows
        tiles=[]
        for row in display[:24]:
            cols=[]
            for i in row['preview_frames']:
                bgr=cv2.imread(str(ctx.run/'source'/f'{i:05d}.png'))
                mask=cv2.imread(str(ctx.run/'tracking'/f'object_{row["id"]:03d}'/f'{i:05d}.png'),0)
                base=(bgr*.25).astype(np.uint8)
                base[mask>127]=(bgr[mask>127]*.6+np.array([40,120,40])).clip(0,255).astype(np.uint8)
                tile=cv2.resize(base,(320,round(320*wh/ww)),interpolation=cv2.INTER_AREA)
                label=np.zeros((30,320,3),np.uint8)
                cv2.putText(label,f'ID {row["id"]} | frame {i+1}',(8,21),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1,cv2.LINE_AA)
                cols.append(np.vstack([label,tile]))
            tiles.append(np.hstack(cols))
        preview=ctx.run/'subject_preview.jpg'
        preview.unlink(missing_ok=True)
        if tiles:cv2.imwrite(str(preview),np.vstack(tiles),[cv2.IMWRITE_JPEG_QUALITY,94])
        request={'schema':1,'status':'awaiting_subject','message':'Choose exactly one subject ID, or add positive/negative points in original source pixel coordinates.',
                 'candidates':rows,'preview':ctx.relative(preview) if preview.exists() else None,
                 'source_resolution':[source['width'],source['height']],'working_resolution':[ww,wh],
                 'points_schema':[{'frame':0,'positive':[[0,0]],'negative':[]}],
                 'reason':'multiple_candidates' if len(credible)>1 else 'no_credible_candidate'}
        atomic_json(ctx.run/'awaiting_subject.json',request)
        ctx.progress('selection',0,request['message'],status='awaiting_subject',candidates=rows,preview=request['preview'])
        raise AwaitingSubject(request['message'])
    (ctx.run/'awaiting_subject.json').unlink(missing_ok=True)
    atomic_json(ctx.cache/'selected_subject.json',{'id':selected['id'],'candidate':selected,
        'selection':'points' if ctx.settings['subject_points'] else 'explicit' if ctx.settings['subject_id'] is not None else 'single_credible_candidate'})
    return selected['id']

def export_depth(ctx,key):
    import cv2
    import numpy as np
    import OpenEXR
    from scipy.ndimage import distance_transform_edt
    source=read_json(ctx.cache/'decode.json')['data'];n=source['frame_count'];w=source['working_width'];h=source['working_height']
    q=np.load(ctx.run/'depth'/'relative_raw.npy',mmap_mode='r')
    if q.shape!=(n,h,w):raise ValueError('Raw depth shape mismatch')
    radius=ctx.settings['edge_radius'];inset=ctx.settings['edge_inset'];strength=ctx.settings['cleanup_strength']
    for folder in ['raw_z','clean_z']:clear_owned(ctx,ctx.run/'depth'/folder)
    outalpha=ctx.run/'alpha';clear_owned(ctx,outalpha)
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*radius+1,)*2)
    inset_kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*inset+1,)*2)
    samples=[];alpha_stats=[]
    for i in range(n):
        ctx.cancel();a=read_coverage(ctx.run/'matte'/f'{i:05d}.png',w,h)
        scale=coverage_scale(a)
        if i and a.dtype!=alpha_dtype:raise ValueError('Matte precision changed across frames')
        alpha_dtype=a.dtype
        if inset:a=cv2.erode(a,inset_kernel)
        core=cv2.erode((a>=250*scale).astype(np.uint8),kernel)>0
        positive=core&np.isfinite(q[i])&(q[i]>0)
        # Sampling starts on a regular grid but small valid subjects must not
        # disappear merely because they fall between sampled grid points.
        sample=q[i,::8,::8][positive[::8,::8]]
        if sample.size==0 and positive.any():sample=q[i][positive]
        if sample.size:samples.append(sample)
        alpha_stats.append({'frame':i,'support_pixels':int((a>5*scale).sum()),'core_pixels':int(core.sum())})
        if not cv2.imwrite(str(outalpha/f'{i:05d}.png'),a,[cv2.IMWRITE_PNG_COMPRESSION,2]):raise IOError('Alpha write failed')
    if not samples:
        raise ValueError('No confident subject core; supplied mattes need opaque foreground pixels (at least 250/255), and edge inset/radius must leave a core'
                         if ctx.settings['matte_source']=='custom' else
                         'No confident subject core; reduce edge inset/radius or select a larger subject')
    median=float(np.median(np.concatenate(samples)))
    if not math.isfinite(median) or median<=0:raise ValueError('Invalid whole-shot inverse-depth median')
    header={'compression':OpenEXR.ZIP_COMPRESSION,'type':OpenEXR.scanlineimage}
    max_interior=0.;replaced=0;no_core=[];q_clamped=[]
    for i in range(n):
        ctx.cancel();values=np.asarray(q[i]);a=read_coverage(outalpha/f'{i:05d}.png',w,h);scale=coverage_scale(a)
        if not np.isfinite(values).all():raise ValueError('Nonfinite raw VDA output')
        # Official relative inference can clamp to zero; avoid inf in float EXRs
        # and report this explicit safety floor rather than silently hiding it.
        floor=max(median*1e-6,1e-8);clamped=int((values<floor).sum())
        if clamped:q_clamped.append({'frame':i,'pixels':clamped})
        raw=np.asarray(median/np.maximum(values,floor),dtype=np.float32)
        core=cv2.erode((a>=250*scale).astype(np.uint8),kernel)>0
        support=a>5*scale;clean=raw.copy()
        if core.any():
            nearest=distance_transform_edt(~core,return_distances=False,return_indices=True)
            extended=raw[tuple(nearest)]
            boundary=support&~core
            clean[~core]=(1-strength)*raw[~core]+strength*extended[~core]
            replaced+=int(boundary.sum()) if strength else 0
            max_interior=max(max_interior,float(np.max(np.abs(clean[core]-raw[core]))))
        elif support.any():no_core.append(i)
        for folder,array in [('raw_z',raw),('clean_z',clean)]:
            OpenEXR.File(header,{'R':array,'G':array,'B':array}).write(str(ctx.run/'depth'/folder/f'{i:05d}.exr'))
        if i%4==0:ctx.progress('export',i/n,f'Exporting depth {i+1}/{n}',frame=i+1,frame_count=n)
    files=[*sequence_files(outalpha,n,'.png'),*sequence_files(ctx.run/'depth/raw_z',n,'.exr'),*sequence_files(ctx.run/'depth/clean_z',n,'.exr')]
    return ctx.finish_stage('export',key,files,{'q_median':median,'unit_z_formula':'unitZ = whole-shot confident-foreground median(q) / max(q, median(q)*1e-6)',
        'cleanup':'Nearest confident foreground extension; blend outside eroded core using cleanup_strength; no interior or temporal smoothing.',
        'cleanup_limitations':'Boundary extension assumes local depth continuity and does not recover hidden surfaces or new depth evidence.',
        'edge_radius':radius,'edge_inset':inset,'cleanup_strength':strength,'max_core_change':max_interior,
        'alpha_bit_depth':alpha_dtype.itemsize*8,'core_alpha_threshold_normalized':250/255,'support_alpha_threshold_normalized':5/255,
        'boundary_pixels_blended':replaced,'frames_with_subject_but_no_core':no_core,'q_floor_events':q_clamped,'alpha_statistics':alpha_stats})

def validate_and_finish(ctx,rows,source_hash,configuration_key,start):
    import cv2
    import numpy as np
    import OpenEXR
    validate_job(ctx.job)
    src=rows['decode']['data'];n=src['frame_count'];w=src['working_width'];h=src['working_height']
    for i in range(n):
        ctx.cancel()
        rgb=cv2.imread(str(ctx.run/'source'/f'{i:05d}.png'));alpha=read_coverage(ctx.run/'alpha'/f'{i:05d}.png',w,h)
        if rgb is None or rgb.shape!=(h,w,3) or alpha is None or alpha.shape!=(h,w):raise ValueError('Final PNG validation failed')
        for name in ['raw_z','clean_z']:
            with OpenEXR.File(str(ctx.run/'depth'/name/f'{i:05d}.exr')) as exr:
                channels=exr.channels();pixels=channels['RGB'].pixels if 'RGB' in channels else channels['R'].pixels
                if pixels.shape not in [(h,w),(h,w,3)] or pixels.dtype!=np.float32 or not np.isfinite(pixels).all() or np.any(pixels<=0):
                    raise ValueError('Final float32 EXR validation failed')
                if pixels.ndim==3 and (not np.array_equal(pixels[:,:,0],pixels[:,:,1]) or not np.array_equal(pixels[:,:,0],pixels[:,:,2])):
                    raise ValueError('Depth EXR channels disagree')
        if i%8==0:ctx.progress('validation',i/n,f'Validating frame {i+1}/{n}',frame=i+1,frame_count=n)
    if file_hash(ctx.job['source'],ctx.cancel)!=source_hash:raise ValueError('Source changed during processing')
    custom=ctx.settings['matte_source']=='custom'
    if custom:
        from supplied_matte import verify_sequence
        verify_sequence(ctx,rows['matte']['data'])
    selected=read_json(ctx.cache/'selected_subject.json')
    manifest={'schema':1,'type':'local-depth-reference','status':'complete',
        'source':{k:v for k,v in src.items() if k not in ['proxy','working_width','working_height','timestamps_seconds']},
        'processing':{'working_width':w,'working_height':h,'q_median':rows['export']['data']['q_median'],
                      'depth_model':'Video Depth Anything Small relative','matte_model':'Supplied image sequence' if custom else 'SAM3 + MatAnyone2',
                      'matte_source':ctx.settings['matte_source'],'alpha_bit_depth':rows['export']['data']['alpha_bit_depth'],
                      'selected_subject':selected,'settings':ctx.settings,'normalization':rows['export']['data'],
                      'depth_inference':rows['depth']['data'],'matte_inference':rows['matte']['data'],
                      'tracking_inference':None if custom else rows['tracking']['data'],
                      'source_preserved':True,'frame_offset':0,'beeble_data_used':None if custom else False,'pose_used':False,
                      'matte_provenance_note':'User-supplied matte; its original creator is not inferred from the filename.' if custom else 'Automatic local SAM3 + MatAnyone2 matte.',
                      'scale_convention':'Relative unit depth, manually calibrated in Blender; not measured meters',
                      'seconds_total':time.perf_counter()-start},
        'paths':{'raw_depth':'depth/raw_z/00000.exr','clean_depth':'depth/clean_z/00000.exr',
                 'alpha':'alpha/00000.png','source':'source/00000.png'},
        'sequence':{'first_index':0,'count':n,'digits':5},'binding':ctx.job['binding'],
        'configuration_key':configuration_key,'worker_code_sha256':code_hash(),
        'validation':{'all_frames_decoded':True,'all_pngs_valid':True,'all_float32_exrs_finite_positive':True,
                      'all_depth_channels_identical':True,'source_hash_unchanged':True,
                      'visual_review':'Pending user review in Blender; automated validation does not certify matte/depth accuracy'},
        'stage_cache':{k:{'key':r['key'],'artifacts':r['artifacts']} for k,r in rows.items()}}
    ctx.cancel();validate_job(ctx.job);atomic_json(ctx.run/'manifest.json',manifest)
    ctx.progress('validation',1,'Depth reference ready',status='complete',manifest='manifest.json')
    return manifest

def pipeline(ctx):
    start=time.perf_counter();ctx.cancel()
    ctx.progress('decode',0,'Checking source, models and stage cache')
    source_hash=file_hash(ctx.job['source'],ctx.cancel);code=code_hash()
    custom=ctx.settings['matte_source']=='custom'
    model_keys=['vda_checkpoint'] if custom else ['sam3_checkpoint','matanyone_checkpoint','vda_checkpoint']
    runtime_keys=['vda_repo'] if custom else ['sam3_module_root','matanyone_root','vda_repo']
    models={key:file_hash(ctx.runtime[key],ctx.cancel) for key in model_keys}
    runtime_code={key:tree_hash(ctx.runtime[key]) for key in runtime_keys}
    common={'source':source_hash,'worker':code}
    configuration_key=digest({'common':common,'settings':ctx.settings,'models':models,'runtime_code':runtime_code})
    rows={};decode_key=digest({'common':common,'working_max_width':ctx.settings['working_max_width']})
    rows['decode']=ctx.cache_record('decode',decode_key) or decode(ctx,decode_key)
    if custom:
        from supplied_matte import inspect_sequence,import_sequence
        snapshot=inspect_sequence(ctx,rows['decode']['data'])
        matte_key=digest({'decode':decode_key,'source_type':'custom','sequence':snapshot})
        configuration_key=digest({'configuration':configuration_key,'custom_matte':snapshot})
        rows['matte']=ctx.cache_record('matte',matte_key) or import_sequence(ctx,matte_key,snapshot)
        (ctx.run/'awaiting_subject.json').unlink(missing_ok=True)
        atomic_json(ctx.cache/'selected_subject.json',{'id':None,'selection':'custom_matte',
                    'first_frame':snapshot['first_frame'],'sequence_sha256':digest(snapshot)})
    else:
        track_key=digest({'decode':decode_key,'model':models['sam3_checkpoint'],'runtime':runtime_code['sam3_module_root'],
                          'prompt':ctx.settings['subject_prompt'],'points':ctx.settings['subject_points']})
        rows['tracking']=ctx.cache_record('tracking',track_key) or run_child(ctx,'tracking',track_key)
        selected=choose_subject(ctx,rows['decode'],rows['tracking'])
        matte_key=digest({'tracking':track_key,'selected_id':selected,'model':models['matanyone_checkpoint'],
                         'runtime':runtime_code['matanyone_root'],'settings':{k:ctx.settings[k] for k in ['matte_short_side','matte_warmup','matte_reset_interval']}})
        rows['matte']=ctx.cache_record('matte',matte_key) or run_child(ctx,'matte',matte_key)
    depth_key=digest({'decode':decode_key,'model':models['vda_checkpoint'],'runtime':runtime_code['vda_repo'],'input_size':ctx.settings['vda_input_size']})
    rows['depth']=ctx.cache_record('depth',depth_key) or run_child(ctx,'depth',depth_key)
    export_key=digest({'depth':depth_key,'matte':matte_key,'edge':{k:ctx.settings[k] for k in ['edge_radius','edge_inset','cleanup_strength']}})
    rows['export']=ctx.cache_record('export',export_key) or export_depth(ctx,export_key)
    result=validate_and_finish(ctx,rows,source_hash,configuration_key,start)
    print(json.dumps({'status':'complete','manifest':str(ctx.run/'manifest.json'),'frames':result['sequence']['count']}),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--internal-stage',choices=['tracking','matte','depth']);parser.add_argument('--key')
    args=parser.parse_args();ctx=None;lock=None;owns_progress=False
    try:
        job=validate_job(read_json(args.job));manifest=Path(job['run_dir'])/'manifest.json'
        # Completed versions are immutable, including their progress and cache.
        if manifest.exists():
            completed=read_json(manifest)
            if completed.get('status')!='complete' or completed.get('type')!='local-depth-reference':
                raise ValueError('Existing manifest is not a completed depth reference; refusing to overwrite it')
            print(json.dumps({'status':'existing_complete','manifest':str(manifest),
                              'message':'Completed version is immutable; reserve a new version to process again.'}),flush=True)
            return 0
        ctx=Context(job)
        if args.internal_stage:
            owns_progress=True
            from stages import tracking,matte,depth
            {'tracking':tracking,'matte':matte,'depth':depth}[args.internal_stage](ctx,args.key)
        else:
            lock=acquire_lock(ctx);owns_progress=True;pipeline(ctx)
        return 0
    except AwaitingSubject:return 20
    except Cancelled as exc:
        if ctx and owns_progress:ctx.progress('validation',0,str(exc),status='cancelled')
        return 21
    except Exception as exc:
        traceback.print_exc()
        if ctx and owns_progress:ctx.progress('validation',0,str(exc),status='error',error_type=type(exc).__name__)
        return 1
    finally:
        if lock:
            try:
                if read_json(lock).get('pid')==os.getpid():lock.unlink()
            except OSError:pass

if __name__=='__main__':sys.exit(main())

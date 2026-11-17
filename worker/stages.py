"""Model stages launched in configured external Python environments."""
import gc
import math
from pathlib import Path
import sys
import time
from common import read_json,sequence_files,file_hash

def tracking(ctx,key):
    from process import clear_owned
    import cv2
    import numpy as np
    import torch
    ctx.cancel();ctx.progress('tracking',0,'Loading SAM3 subject tracker')
    src=read_json(ctx.cache/'decode.json')['data'];n=src['frame_count'];w=src['working_width'];h=src['working_height']
    prompts=ctx.settings['subject_points']
    for pr in prompts:
        frame=pr.get('frame',0)
        if isinstance(frame,bool) or not isinstance(frame,int) or not 0<=frame<n:raise ValueError('Point prompt frame is outside source')
        if not pr.get('positive'):raise ValueError('Each point prompt requires a positive point')
        for xy in pr['positive']+pr.get('negative',[]):
            if len(xy)!=2 or not(0<=xy[0]<src['width'] and 0<=xy[1]<src['height']):raise ValueError('Point prompt lies outside original oriented source pixels')
    if not torch.cuda.is_available():raise RuntimeError('SAM3 requires an available CUDA GPU in the matting runtime')
    sys.path.insert(0,str(Path(ctx.runtime['sam3_module_root']).resolve()))
    from sam3_lib.sam3_video_predictor import Sam3VideoPredictor
    dest=ctx.run/'tracking';clear_owned(ctx,dest);start=time.perf_counter()
    model=Sam3VideoPredictor(checkpoint_path=ctx.runtime['sam3_checkpoint'],enable_inst_interactivity=True)
    ctx.cancel();seen={};areas={};completed=set()
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
        sid=model.start_session(str(ctx.run/src['proxy']))['session_id']
        try:
            if prompts:
                for pr in prompts:
                    positive=pr['positive'];negative=pr.get('negative',[])
                    model.add_prompt(sid,pr.get('frame',0),
                        points=[[float(x)/src['width'],float(y)/src['height']] for x,y in positive+negative],
                        point_labels=[1]*len(positive)+[0]*len(negative),obj_id=0)
                start_frame=min(pr.get('frame',0) for pr in prompts)
            else:
                start_frame=0;model.add_prompt(sid,0,text=ctx.settings['subject_prompt'])
            ctx.cancel()
            for result in model.propagate_in_video(sid,'both' if start_frame else 'forward',start_frame,None):
                ctx.cancel();f=int(result['frame_index'])
                if not 0<=f<n:raise ValueError('Tracker returned an out-of-range frame')
                out=result['outputs'];completed.add(f)
                for oid,mask in zip(out['obj_ids'],out['out_binary_masks']):
                    oid=int(oid)
                    if prompts and oid!=0:continue
                    m=mask.detach().cpu().numpy() if torch.is_tensor(mask) else np.asarray(mask)
                    m=np.squeeze(m).astype(np.uint8)*255
                    if m.shape!=(h,w):raise ValueError('SAM3 mask size does not match tracking proxy')
                    folder=dest/f'object_{oid:03d}';folder.mkdir(exist_ok=True)
                    if not cv2.imwrite(str(folder/f'{f:05d}.png'),m):raise IOError('Tracking mask write failed')
                    seen.setdefault(oid,set()).add(f);areas.setdefault(oid,[0]*n)[f]=int(np.count_nonzero(m))
                ctx.progress('tracking',len(completed)/n,f'Tracking frame {len(completed)}/{n}',frame=len(completed),frame_count=n)
        finally:model.close_session(sid)
    files=[];candidates=[];minimum_area=max(64,int(w*h*.00025));minimum_frames=max(1,min(3,math.ceil(n*.05)))
    for oid in sorted(seen):
        folder=dest/f'object_{oid:03d}'
        for f in set(range(n))-seen[oid]:
            ctx.cancel()
            if not cv2.imwrite(str(folder/f'{f:05d}.png'),np.zeros((h,w),np.uint8)):raise IOError('Missing mask fill failed')
        files.extend(sequence_files(folder,n,'.png'))
        ar=np.array(areas[oid]);visible=np.flatnonzero(ar>0)
        candidates.append({'id':oid,'credible':bool((ar>=minimum_area).sum()>=minimum_frames),
             'nonempty_frames':int((ar>0).sum()),'credible_frames':int((ar>=minimum_area).sum()),
             'maximum_area_pixels':int(ar.max()),'maximum_area_fraction':float(ar.max()/(w*h)),
             'preview_frames':[int(visible[0]),int(np.argmax(ar)),int(visible[-1])] if len(visible) else [0,n//2,n-1]})
    data={'candidates':candidates,'frames':n,'width':w,'height':h,'seconds':time.perf_counter()-start,
          'prompt':ctx.settings['subject_prompt'],'point_prompted':bool(prompts),
          'credibility_rule':{'minimum_area_pixels':minimum_area,'minimum_frames':minimum_frames},
          'checkpoint_sha256':file_hash(ctx.runtime['sam3_checkpoint'],ctx.cancel),'torch':torch.__version__,
          'warning':'Candidate credibility is a size/coverage filter, not semantic certainty. Multiple candidates require explicit selection.'}
    ctx.finish_stage('tracking',key,files,data)

def matte(ctx,key):
    from process import clear_owned
    import cv2
    import numpy as np
    import torch
    ctx.cancel();ctx.progress('matte',0,'Loading MatAnyone2 refinement model')
    src=read_json(ctx.cache/'decode.json')['data'];n=src['frame_count'];w=src['width'];h=src['height'];ww=src['working_width'];wh=src['working_height']
    oid=read_json(ctx.cache/'selected_subject.json')['id']
    sys.path[:0]=[ctx.runtime['matanyone_root']]+([ctx.runtime['matanyone_pydeps']] if ctx.runtime.get('matanyone_pydeps') else [])
    from matanyone2.utils.get_default_model import get_matanyone2_model
    from matanyone2.inference.inference_core import InferenceCore
    torch.set_num_threads(8);cv2.setNumThreads(4)
    if not torch.cuda.is_available():raise RuntimeError('MatAnyone2 requires CUDA in the matting runtime')
    start=time.perf_counter();model=get_matanyone2_model(ctx.runtime['matanyone_checkpoint'],torch.device('cuda'))
    model.cfg.max_internal_size=-1;ctx.cancel()
    factor=min(1,ctx.settings['matte_short_side']/min(w,h));iw=max(1,round(w*factor));ih=max(1,round(h*factor))
    dest=ctx.run/'matte';clear_owned(ctx,dest);cap=cv2.VideoCapture(ctx.job['source']);processor=None;last_reset=-100000;resets=[];step_calls=0
    try:
        for f in range(n):
            ctx.cancel();ok,bgr=cap.read()
            if not ok or bgr.shape[:2]!=(h,w):raise ValueError('Source changed or decoding failed during refinement')
            seed_work=cv2.imread(str(ctx.run/'tracking'/f'object_{oid:03d}'/f'{f:05d}.png'),0)
            if seed_work is None or seed_work.shape!=(wh,ww):raise ValueError('Selected subject mask missing')
            mask=cv2.resize(seed_work,(w,h),interpolation=cv2.INTER_NEAREST)
            result=np.zeros((wh,ww),np.uint8)
            if np.count_nonzero(mask)>64:
                small=cv2.resize(bgr,(iw,ih),interpolation=cv2.INTER_AREA)
                im=torch.from_numpy(cv2.cvtColor(small,cv2.COLOR_BGR2RGB)).permute(2,0,1).float().cuda()/255
                seed=cv2.resize(mask,(iw,ih),interpolation=cv2.INTER_NEAREST)
                with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
                    reset=processor is None or f-last_reset>=ctx.settings['matte_reset_interval']
                    reason='initial' if processor is None else 'periodic'
                    if not reset:
                        pred=processor.step(im);step_calls+=1
                        alpha=processor.output_prob_to_mask(pred).cpu().numpy();a=alpha>.5;s=seed>127
                        intersection=np.count_nonzero(a&s)
                        reset=intersection/max(1,np.count_nonzero(a|s))<.85 or intersection/max(1,np.count_nonzero(s))<.97
                        reason='quality_guard'
                    if reset:
                        if processor is not None:del processor
                        processor=InferenceCore(model,cfg=model.cfg,device='cuda')
                        processor.step(im,torch.from_numpy(seed.copy()).float().cuda(),objects=[1],delete_buffer=False)
                        for warm in range(ctx.settings['matte_warmup']):
                            ctx.cancel();pred=processor.step(im,first_frame_pred=True,delete_buffer=warm==ctx.settings['matte_warmup']-1)
                        step_calls+=1+ctx.settings['matte_warmup'];alpha=processor.output_prob_to_mask(pred).cpu().numpy()
                        last_reset=f;resets.append({'frame':f,'reason':reason})
                alpha=cv2.resize(alpha,(w,h),interpolation=cv2.INTER_LINEAR)
                guard=cv2.GaussianBlur(cv2.dilate(mask,np.ones((65,65),np.uint8)),(0,0),5)/255
                alpha=np.clip(alpha,0,1)*guard
                result=np.round(np.clip(cv2.resize(alpha,(ww,wh),interpolation=cv2.INTER_AREA),0,1)*255).astype(np.uint8)
                del im,pred
            elif processor is not None:del processor;processor=None
            if not cv2.imwrite(str(dest/f'{f:05d}.png'),result,[cv2.IMWRITE_PNG_COMPRESSION,2]):raise IOError('Refined matte write failed')
            ctx.progress('matte',(f+1)/n,f'Refining subject {f+1}/{n}',frame=f+1,frame_count=n)
        if cap.read()[0]:raise ValueError('Source has more frames than initial decode')
    finally:cap.release()
    data={'selected_id':oid,'frames':n,'native_source_resolution':[w,h],'inference_resolution':[iw,ih],
          'output_resolution':[ww,wh],'seconds':time.perf_counter()-start,'resets':resets,'step_calls':step_calls,
          'checkpoint_sha256':file_hash(ctx.runtime['matanyone_checkpoint'],ctx.cancel),'torch':torch.__version__,
          'source_rgb':'Original full-resolution decoded source; no proxy RGB used for refinement',
          'postprocess':'Soft seed guard only; no largest-component removal, hole filling or temporal averaging'}
    ctx.finish_stage('matte',key,sequence_files(dest,n,'.png'),data)

def depth(ctx,key):
    import cv2
    import numpy as np
    import torch
    ctx.cancel();ctx.progress('depth',0,'Loading Video Depth Anything Small')
    src=read_json(ctx.cache/'decode.json')['data'];n=src['frame_count'];w=src['working_width'];h=src['working_height']
    fps=src['fps_num']/src['fps_den']
    try:
        import psutil
        if n*w*h*28>psutil.virtual_memory().available*.7:
            raise MemoryError('Clip exceeds available VDA host memory. Lower working_max_width or split the clip.')
    except ImportError:pass
    frames=[]
    for i in range(n):
        ctx.cancel();bgr=cv2.imread(str(ctx.run/'source'/f'{i:05d}.png'))
        if bgr is None or bgr.shape!=(h,w,3):raise ValueError('Working RGB sequence is incomplete')
        frames.append(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB))
    frames=np.stack(frames);cv2.setNumThreads(4);torch.set_num_threads(8);np.random.seed(0);torch.manual_seed(0)
    sys.path.insert(0,ctx.runtime['vda_repo'])
    from video_depth_anything.video_depth import VideoDepthAnything
    if not torch.cuda.is_available():raise RuntimeError('VDA runtime requires CUDA')
    model=VideoDepthAnything(encoder='vits',features=64,out_channels=[48,96,192,384],metric=False)
    state=torch.load(ctx.runtime['vda_checkpoint'],map_location='cpu',weights_only=True);model.load_state_dict(state,strict=True)
    model=model.to('cuda').eval();ctx.cancel();original_forward=model.forward;windows=math.ceil(n/22);calls=0
    def cooperative_forward(x):
        nonlocal calls
        ctx.cancel();out=original_forward(x);calls+=1
        ctx.progress('depth',calls/windows,f'Depth window {calls}/{windows}',window=calls,windows=windows)
        ctx.cancel();return out
    model.forward=cooperative_forward
    torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();start=time.perf_counter()
    with torch.inference_mode():q,outfps=model.infer_video_depth(frames,fps,input_size=ctx.settings['vda_input_size'],device='cuda',fp32=False)
    torch.cuda.synchronize();seconds=time.perf_counter()-start
    if q.shape!=(n,h,w) or q.dtype!=np.float32 or not np.isfinite(q).all() or outfps!=fps:raise ValueError('VDA output validation failed')
    dest=ctx.run/'depth';dest.mkdir(exist_ok=True);path=dest/'relative_raw.npy';ctx.cancel();np.save(path,q)
    data={'model':'Video Depth Anything Small relative','encoder':'vits','seed':0,'frames':n,'precision':'CUDA autocast FP16',
          'input_size_short_side':ctx.settings['vda_input_size'],'window':32,'overlap':10,'windows':calls,
          'raw_min':float(q.min()),'raw_max':float(q.max()),'raw_shape':list(q.shape),
          'seconds':seconds,'peak_gpu_gib':torch.cuda.max_memory_allocated()/1024**3,
          'checkpoint_sha256':file_hash(ctx.runtime['vda_checkpoint'],ctx.cancel),'torch':torch.__version__,'cuda':torch.version.cuda,
          'meaning':'Relative inverse-depth. Not metric distance; no normalization per frame.'}
    ctx.finish_stage('depth',key,[path],data)
    del model,state,frames,q;gc.collect();torch.cuda.empty_cache()

"""Opt-in local GPU regression: short generic clip, point selection, cancel/resume."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

WORKER=Path(__file__).resolve().parents[1]/'process.py'
sys.path.insert(0,str(WORKER.parent))
from common import read_json,atomic_json,file_hash

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-config',required=True);parser.add_argument('--source',required=True)
    parser.add_argument('--report',required=True);args=parser.parse_args()
    config=read_json(args.runtime_config);runtime=config.get('runtime',config)
    with tempfile.TemporaryDirectory(prefix='local_depth_worker_test_') as temp:
        root=Path(temp);shot=root/'Test Shot';shot.mkdir();run=shot/'Resources'/'depth map'/'v001';run.mkdir(parents=True)
        source=root/'short_25fps_320x180.mp4'
        subprocess.run([runtime.get('ffmpeg','ffmpeg'),'-v','error','-n','-i',args.source,'-frames:v','12',
             '-vf','scale=320:180','-r','25','-c:v','libx264','-crf','12','-pix_fmt','yuv420p',str(source)],check=True)
        job={'schema':1,'source':str(source),'shot_dir':str(shot),'run_dir':str(run),
             'binding':{'resources_dir':str(shot/'Resources')},'runtime':runtime,
             'settings':{'working_max_width':320,'vda_input_size':112,'matte_short_side':256,
                         'subject_points':[{'frame':0,'positive':[[147,105]],'negative':[[12,90]]}]}}
        path=run/'job.json';atomic_json(path,job)
        command=[runtime['vda_python'],str(WORKER),'--job',str(path)]
        log=run/'test.log';cancel_requested=False;start=time.perf_counter()
        with log.open('w',encoding='utf-8') as stream:
            proc=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT)
            while proc.poll() is None:
                progress=read_json(run/'progress.json') if (run/'progress.json').exists() else {}
                if progress.get('stage')=='matte' and 0<progress.get('stage_fraction',0)<1 and not cancel_requested:
                    (run/'cancel.flag').touch();cancel_requested=True
                if time.perf_counter()-start>300:
                    (run/'cancel.flag').touch();raise TimeoutError('GPU fixture exceeded five minutes')
                time.sleep(.02)
        if not cancel_requested or proc.returncode!=21 or (run/'manifest.json').exists():
            raise AssertionError('Cooperative cancellation test failed: '+log.read_text()[-5000:])
        decode_before=file_hash(run/'_cache/decode.json');track_before=file_hash(run/'_cache/tracking.json')
        (run/'cancel.flag').unlink()
        with log.open('a',encoding='utf-8') as stream:result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
        if result.returncode!=0:raise AssertionError('Resume failed: '+log.read_text()[-5000:])
        manifest=read_json(run/'manifest.json')
        assert manifest['sequence']['count']==12
        assert (manifest['source']['fps_num'],manifest['source']['fps_den'])==(25,1)
        assert (manifest['processing']['working_width'],manifest['processing']['working_height'])==(320,180)
        assert manifest['processing']['selected_subject']['id']==0
        assert manifest['processing']['selected_subject']['selection']=='points'
        assert file_hash(run/'_cache/decode.json')==decode_before and file_hash(run/'_cache/tracking.json')==track_before
        manifest_hash=file_hash(run/'manifest.json');progress_hash=file_hash(run/'progress.json')
        repeat=subprocess.run(command,capture_output=True,text=True,timeout=20)
        assert repeat.returncode==0 and 'existing_complete' in repeat.stdout
        assert file_hash(run/'manifest.json')==manifest_hash and file_hash(run/'progress.json')==progress_hash
        atomic_json(args.report,{'status':'passed','source_fixture':'12 frames, 320x180, 25/1fps, generated from authorized original RGB',
            'source_fixture_sha256':manifest['source']['sha256'],'point_selection':True,'cancel_during_matting':True,
            'resume_reused_decode_and_tracking':True,'immutable_manifest_and_progress':True,
            'frame_count':12,'fps_num':25,'fps_den':1,'working_resolution':[320,180],
            'validation':manifest['validation'],'processing_seconds':manifest['processing']['seconds_total'],
            'elapsed_test_seconds':time.perf_counter()-start,'worker_code_sha256':manifest['worker_code_sha256']})
        print(Path(args.report).resolve())

if __name__=='__main__':main()

"""Real local codec/export tests; model inference is covered by the authorized shot run."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from common import Context,AwaitingSubject,Cancelled,atomic_json,read_json
from process import decode,choose_subject,export_depth

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.shot=self.root/'Shot';self.shot.mkdir();self.inputs=self.root/'input';self.inputs.mkdir()
        for i in range(4):
            im=np.zeros((64,96,3),np.uint8);im[:]=[20+i*30,60,150]
            im[12:45,20+i*4:60+i*4]=[200,120,25]
            cv2.imwrite(str(self.inputs/f'{i:05d}.png'),im)
        self.source=self.root/'source.mp4'
        self.ffmpeg('-framerate','30000/1001','-i',str(self.inputs/'%05d.png'),'-frames:v','4','-c:v','libx264','-pix_fmt','yuv420p',str(self.source))
        self.job={'source':str(self.source),'shot_dir':str(self.shot),'run_dir':str(self.shot/'resources/depth map/v001'),
                  'runtime':{},'settings':{'working_max_width':80}}
        self.ctx=Context(self.job)
    def tearDown(self):self.temp.cleanup()
    def ffmpeg(self,*args):subprocess.run(['ffmpeg','-v','error','-n',*args],check=True,capture_output=True)
    def test_exact_rational_fps_frames_and_generic_dimensions(self):
        row=decode(self.ctx,'decode-key');data=row['data']
        self.assertEqual((data['fps_num'],data['fps_den'],data['frame_count']),(30000,1001,4))
        self.assertEqual((data['width'],data['height']),(96,64))
        self.assertEqual((data['working_width'],data['working_height']),(80,52))
        cap=cv2.VideoCapture(str(self.source))
        for i in range(4):
            ok,bgr=cap.read();self.assertTrue(ok)
            expected=cv2.resize(bgr,(80,52),interpolation=cv2.INTER_AREA)
            self.assertTrue(np.array_equal(expected,cv2.imread(str(self.ctx.run/'source'/f'{i:05d}.png'))))
        cap.release()
        self.assertFalse((self.ctx.run/'manifest.json').exists())
    def test_rotation_fails_clearly_before_output(self):
        rotated=self.root/'rotated.mp4'
        self.ffmpeg('-display_rotation','90','-i',str(self.source),'-c','copy',str(rotated))
        ctx=Context({**self.job,'source':str(rotated)})
        with self.assertRaisesRegex(ValueError,'Rotated video'):decode(ctx,'rotated')
        self.assertFalse((ctx.run/'manifest.json').exists())
    def test_non_square_pixels_fail_clearly(self):
        nonsquare=self.root/'nonsquare.mp4'
        self.ffmpeg('-i',str(self.source),'-vf','setsar=2/1','-c:v','libx264',str(nonsquare))
        with self.assertRaisesRegex(ValueError,'Non-square-pixel'):decode(Context({**self.job,'source':str(nonsquare)}),'sar')
    def test_cancel_during_decode_no_valid_cache(self):
        count=0
        def cancel():
            nonlocal count
            count+=1
            if count==3:raise Cancelled('test interruption')
        self.ctx.cancel=cancel
        with self.assertRaises(Cancelled):decode(self.ctx,'cancelled')
        self.assertFalse((self.ctx.cache/'decode.json').exists())
        self.assertFalse((self.ctx.run/'manifest.json').exists())
    def test_selection_requires_choice_for_multiple_and_accepts_explicit_id(self):
        row=decode(self.ctx,'decode');n=row['data']['frame_count'];w=row['data']['working_width'];h=row['data']['working_height']
        candidates=[]
        for oid in [1,7]:
            path=self.ctx.run/'tracking'/f'object_{oid:03d}';path.mkdir(parents=True)
            for i in range(n):cv2.imwrite(str(path/f'{i:05d}.png'),np.full((h,w),255,np.uint8))
            candidates.append({'id':oid,'credible':True,'nonempty_frames':4,'preview_frames':[0,2,3]})
        with self.assertRaises(AwaitingSubject):choose_subject(self.ctx,row,{'data':{'candidates':candidates}})
        self.assertEqual(read_json(self.ctx.run/'progress.json')['status'],'awaiting_subject')
        self.assertTrue((self.ctx.run/'subject_preview.jpg').exists())
        self.ctx.settings['subject_id']=7
        self.assertEqual(choose_subject(self.ctx,row,{'data':{'candidates':candidates}}),7)
        self.assertFalse((self.ctx.run/'awaiting_subject.json').exists())
        self.ctx.settings['subject_id']=None
        self.assertEqual(choose_subject(self.ctx,row,{'data':{'candidates':candidates[:1]}}),1)
    def test_global_normalization_cleanup_and_empty_frame(self):
        import OpenEXR
        row=decode(self.ctx,'decode');n=4;w=row['data']['working_width'];h=row['data']['working_height']
        folder=self.ctx.run/'matte';folder.mkdir();(self.ctx.run/'depth').mkdir()
        q=np.ones((n,h,w),np.float32)
        for i,value in enumerate([2,4,8,16]):
            a=np.zeros((h,w),np.uint8)
            if i<3:a[10:42,16:64]=255
            cv2.imwrite(str(folder/f'{i:05d}.png'),a)
            q[i,10:42,16:64]=value
            q[i,10:12,16:64]=1 # deliberately contaminated boundary, outside eroded core
        np.save(self.ctx.run/'depth/relative_raw.npy',q)
        result=export_depth(self.ctx,'export')
        self.assertEqual(result['data']['q_median'],4)
        self.assertEqual(result['data']['max_core_change'],0)
        def pixels(kind,i):
            with OpenEXR.File(str(self.ctx.run/'depth'/kind/f'{i:05d}.exr')) as f:return f.channels()['RGB'].pixels.copy()
        for i,z in enumerate([2,1,.5]):self.assertAlmostEqual(float(pixels('raw_z',i)[20,30,0]),z)
        raw=pixels('raw_z',0);clean=pixels('clean_z',0)
        self.assertEqual(float(raw[10,30,0]),4)
        self.assertEqual(float(clean[10,30,0]),2)
        self.assertEqual(clean.dtype,np.float32)
        self.ctx.settings['cleanup_strength']=0
        export_depth(self.ctx,'export-no-clean')
        for i in range(n):self.assertTrue(np.array_equal(pixels('raw_z',i),pixels('clean_z',i)))

if __name__=='__main__':unittest.main()

"""AE export regressions with real float EXRs/16-bit PNGs; no models required."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
import cv2
import numpy as np
import OpenEXR
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ae_export
from common import Cancelled,atomic_json,file_hash,read_json

class AEExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.shot=self.root/'Shot 02';self.shot.mkdir()
        self.sid=str(uuid.uuid4());self.pid=str(uuid.uuid4());self.depth=self.shot/'Resources'/'depth map';self.package=self.depth/'v001';self.package.mkdir(parents=True)
        atomic_json(self.shot/'shot.json',{'schema':'blenderae.shot','schema_version':1,'shot_id':self.sid,'project_id':self.pid})
        self.binding={'binding_kind':'blenderae.shot','manifest_path':str(self.shot/'shot.json'),
                      'shot_dir':str(self.shot),'resources_dir':str(self.shot/'Resources'),'depth_root':str(self.depth),
                      'shot_id':self.sid,'project_id':self.pid}
        self.w=48;self.h=32;self.n=4;self.raw=[];self.clean=[];self.alpha=[];inventory=[]
        for i in range(self.n):
            a=np.zeros((self.h,self.w),np.uint8)
            if i==0:a[10:16,15:21]=255
            else:a[2:30,2:46]=255
            z=np.full((self.h,self.w),20+i,np.float32)+np.arange(self.w,dtype=np.float32)[None,:]*.01
            z[a>0]=(8 if i==0 else 1+i*.25)+np.broadcast_to(np.arange(self.w,dtype=np.float32)[None,:]*.001,z.shape)[a>0]
            clean=np.where(a>0,z,np.median(z[a>0])).astype(np.float32)
            self.raw.append(z);self.clean.append(clean);self.alpha.append(a)
            for folder,im in [('depth/raw_z',z),('depth/clean_z',clean)]:
                path=self.package/folder/f'{i:05d}.exr';self.write_exr(path,im);inventory.append(self.record(path))
            for folder,im in [('alpha',a),('source',np.full((self.h,self.w,3),80+i,np.uint8))]:
                path=self.package/folder/f'{i:05d}.png';path.parent.mkdir(parents=True,exist_ok=True);cv2.imwrite(str(path),im);inventory.append(self.record(path))
        self.manifest=self.package/'manifest.json'
        atomic_json(self.manifest,{'schema':1,'type':'local-depth-reference','status':'complete',
            'source':{'path':str(self.root/'original_video_NO_LONGER_PRESENT.mov'),'sha256':'f'*64,'width':96,'height':64,
                      'fps_num':24000,'fps_den':1001,'frame_count':self.n,'pixel_aspect':1},
            'processing':{'working_width':self.w,'working_height':self.h,'q_median':2.0},
            'sequence':{'first_index':0,'count':self.n,'digits':5},'binding':self.binding,
            'paths':{'raw_depth':'depth/raw_z/00000.exr','clean_depth':'depth/clean_z/00000.exr','alpha':'alpha/00000.png','source':'source/00000.png'},
            'stage_cache':{'fixture':{'artifacts':inventory}}})
        self.version=1
    def tearDown(self):self.temp.cleanup()
    def write_exr(self,path,z):
        path.parent.mkdir(parents=True,exist_ok=True)
        OpenEXR.File({'compression':OpenEXR.ZIP_COMPRESSION,'type':OpenEXR.scanlineimage},{'R':z,'G':z,'B':z}).write(str(path))
    def record(self,path):return {'path':path.relative_to(self.package).as_posix(),'bytes':path.stat().st_size,'sha256':file_hash(path)}
    def refresh_record(self,path):
        data=read_json(self.manifest);relative=path.relative_to(self.package).as_posix()
        data['stage_cache']['fixture']['artifacts']=[self.record(path) if item['path']==relative else item for item in data['stage_cache']['fixture']['artifacts']]
        atomic_json(self.manifest,data)
    def job(self,coverage='both'):
        self.version+=1
        return {'schema':1,'kind':'ae_export','source_manifest':str(self.manifest),'source_manifest_sha256':file_hash(self.manifest),
                'shot_dir':str(self.shot),'run_dir':str(self.depth/f'v{self.version:03d}'),'binding':copy.deepcopy(self.binding),
                'runtime':{},'settings':{'ae_coverage':coverage}}
    def test_all_coverage_modes_keep_timing_and_existing_clean_values(self):
        source_before={p:file_hash(p) for p in self.package.rglob('*') if p.is_file()}
        for coverage,keys,png_count in [('whole_scene',{'whole_scene'},4),('person',{'person'},8),('both',{'whole_scene','person'},12)]:
            with self.subTest(coverage=coverage):
                job=self.job(coverage);result=ae_export.export_job(job);run=Path(job['run_dir'])
                self.assertEqual(set(result['streams']),keys);self.assertEqual(result['source']['fps_num'],24000);self.assertEqual(result['source']['fps_den'],1001)
                self.assertEqual(result['sequence'],{'first_index':0,'count':4,'digits':5})
                self.assertEqual(len(list((run/'ae').rglob('*.png'))),png_count)
                self.assertTrue(result['validation']['source_frame_hashes_revalidated_after_export'])
                guide=(run/'AE_IMPORT_GUIDE.txt').read_text();self.assertIn('24000/1001',guide);self.assertIn('zero-based',guide)
                if 'whole_scene' in keys:
                    limits=result['streams']['whole_scene'];expected=np.percentile(np.stack(self.raw),[.5,99.5])
                    np.testing.assert_allclose([limits['near'],limits['far']],expected,rtol=1e-7)
                    for i in range(4):self.assertTrue(np.array_equal(cv2.imread(str(run/'ae/whole_scene'/f'{i:05d}.png'),-1),ae_export.normalize_u16(self.raw[i],limits)))
                if 'person' in keys:
                    limits=result['streams']['person'];self.assertGreater(limits['far'],8)
                    self.assertEqual(limits['far_endpoint_source_index'],0)
                    for i in range(4):
                        data=cv2.imread(str(run/'ae/person'/f'{i:05d}.png'),-1);a=cv2.imread(str(run/'ae/alpha'/f'{i:05d}.png'),-1)
                        self.assertTrue(np.array_equal(data,ae_export.normalize_u16(self.clean[i],limits)))
                        self.assertTrue(np.array_equal(a,self.alpha[i].astype(np.uint16)*257))
                        self.assertTrue(np.any(data[a==0]>0),'Depth must remain unpremultiplied outside alpha')
                self.assertFalse(list((run/'_cache').glob('ae_depth_*.f32')))
        self.assertEqual(source_before,{p:file_hash(p) for p in self.package.rglob('*') if p.is_file()})
    def test_streaming_percentiles_match_numpy_across_chunk_boundaries(self):
        rng=np.random.default_rng(0);values=np.concatenate([rng.lognormal(0,2,1003),np.full(137,2.5)]).astype(np.float32)
        path=self.root/'radix.f32';values.tofile(path)
        with patch.object(ae_export,'CHUNK_VALUES',17):result=ae_export.exact_positive_f32_percentiles(path,len(values),[0,.5,50,99.5,100])
        np.testing.assert_allclose(result,np.percentile(values,[0,.5,50,99.5,100]),rtol=1e-7)
    def test_cancel_mid_export_no_manifest_then_clean_resume(self):
        job=self.job();run=Path(job['run_dir']);requested=False
        def cancel(stage,fraction,ctx):
            nonlocal requested
            if stage=='ae_export' and not requested:(ctx.run/'cancel.flag').touch();requested=True
        with self.assertRaises(Cancelled):ae_export.export_job(job,cancel)
        self.assertFalse((run/'manifest.json').exists());self.assertEqual(read_json(run/'progress.json')['status'],'cancelled')
        self.assertFalse((run/'_cache/worker.lock').exists());self.assertFalse(list((run/'_cache').glob('ae_depth_*.f32')))
        (run/'cancel.flag').unlink();result=ae_export.export_job(job)
        self.assertEqual(result['status'],'complete')
    def test_cancel_during_percentile_cleans_owned_scratch(self):
        job=self.job('whole_scene');run=Path(job['run_dir'])
        def cancel(stage,fraction,ctx):
            if stage=='ae_range':(ctx.run/'cancel.flag').touch()
        with self.assertRaises(Cancelled):ae_export.export_job(job,cancel)
        self.assertFalse((run/'manifest.json').exists());self.assertFalse(list((run/'_cache').glob('ae_depth_*.f32')))
    def test_corrupt_source_inventory_is_rejected(self):
        job=self.job();path=self.package/'depth/raw_z/00000.exr'
        with path.open('ab') as f:f.write(b'corruption')
        with self.assertRaisesRegex(ValueError,'integrity'):ae_export.export_job(job)
        self.assertFalse((Path(job['run_dir'])/'manifest.json').exists())
    def test_nan_wrong_dimensions_and_alpha_type_fail_without_manifest(self):
        for invalid in ['nan','dimensions','alpha']:
            with self.subTest(invalid=invalid):
                raw_path=self.package/'depth/raw_z/00000.exr';alpha_path=self.package/'alpha/00000.png'
                self.write_exr(raw_path,self.raw[0]);cv2.imwrite(str(alpha_path),self.alpha[0]);self.refresh_record(raw_path);self.refresh_record(alpha_path)
                if invalid=='nan':z=self.raw[0].copy();z[0,0]=np.nan;self.write_exr(raw_path,z);self.refresh_record(raw_path)
                elif invalid=='dimensions':self.write_exr(raw_path,self.raw[0][:-1]);self.refresh_record(raw_path)
                else:cv2.imwrite(str(alpha_path),self.alpha[0].astype(np.uint16)*257);self.refresh_record(alpha_path)
                job=self.job('person')
                with self.assertRaises(ValueError):ae_export.export_job(job)
                self.assertFalse((Path(job['run_dir'])/'manifest.json').exists())
    def test_source_frame_modified_during_export_is_detected_at_end(self):
        job=self.job('whole_scene');changed=False
        def alter(stage,fraction,ctx):
            nonlocal changed
            if stage=='ae_export' and not changed:
                path=self.package/'depth/raw_z/00000.exr'
                with path.open('ab') as f:f.write(b'changed after first read')
                changed=True
        with self.assertRaisesRegex(ValueError,'integrity'):ae_export.export_job(job,alter)
        self.assertFalse((Path(job['run_dir'])/'manifest.json').exists())
    def test_manifest_or_binding_changed_mid_export_blocks_publication(self):
        for change in ['source_manifest','binding']:
            with self.subTest(change=change):
                atomic_json(self.shot/'shot.json',{'schema':'blenderae.shot','schema_version':1,'shot_id':self.sid,'project_id':self.pid})
                job=self.job('person');changed=False
                def alter(stage,fraction,ctx):
                    nonlocal changed
                    if stage=='ae_export' and not changed:
                        path=self.manifest if change=='source_manifest' else self.shot/'shot.json';data=read_json(path)
                        if change=='source_manifest':data['changed_while_running']=str(uuid.uuid4())
                        else:data['project_id']=str(uuid.uuid4())
                        atomic_json(path,data);changed=True
                with self.assertRaises(ValueError):ae_export.export_job(job,alter)
                self.assertFalse((Path(job['run_dir'])/'manifest.json').exists())
    def test_wrong_uuid_path_pin_and_completed_destination_are_protected(self):
        for change in ['uuid','path','pin']:
            job=self.job()
            if change=='uuid':job['binding']['shot_id']=str(uuid.uuid4())
            elif change=='path':job['run_dir']=str(self.root/'outside'/'v002')
            else:job['source_manifest_sha256']='0'*64
            with self.assertRaises(ValueError):ae_export.export_job(job)
        job=self.job('whole_scene');ae_export.export_job(job);run=Path(job['run_dir'])
        before={p:file_hash(p) for p in run.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError,'new version'):ae_export.export_job(job)
        self.assertEqual(before,{p:file_hash(p) for p in run.rglob('*') if p.is_file()})
    def test_empty_person_core_and_constant_depth_are_explicit(self):
        for i in range(4):
            p=self.package/'alpha'/f'{i:05d}.png';cv2.imwrite(str(p),np.zeros((self.h,self.w),np.uint8));self.refresh_record(p)
        job=self.job('person')
        with self.assertRaisesRegex(ValueError,'confident subject core'):ae_export.export_job(job)
        for i in range(4):
            p=self.package/'depth/raw_z'/f'{i:05d}.exr';self.write_exr(p,np.full((self.h,self.w),2,np.float32));self.refresh_record(p)
        job=self.job('whole_scene');result=ae_export.export_job(job)
        self.assertTrue(result['streams']['whole_scene']['constant_depth'])
        pixels=cv2.imread(str(Path(job['run_dir'])/'ae/whole_scene/00000.png'),-1)
        self.assertTrue(np.all((pixels==32767)|(pixels==32768)))

if __name__=='__main__':unittest.main()

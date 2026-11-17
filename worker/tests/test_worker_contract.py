"""Meaningful safety/resume tests without model downloads or GPU execution."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import (Context,Cancelled,acquire_lock,atomic_json,read_json,validate_job)

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.shot=self.root/'Project'/'Shots'/'Shot_001';self.shot.mkdir(parents=True)
        self.source=self.root/'source.mp4';self.source.write_bytes(b'test source')
        self.resources=self.shot/'Resources'
        self.job={'schema':1,'source':str(self.source),'shot_dir':str(self.shot),
                  'run_dir':str(self.resources/'depth map'/'v001'),
                  'binding':{'resources_dir':str(self.resources)},'runtime':{}}
    def tearDown(self):self.temp.cleanup()
    def test_resources_case_preserved_and_version_bound(self):
        job=validate_job(self.job)
        self.assertEqual(Path(job['binding']['resources_dir']),self.resources)
        for bad in [self.root/'v001',self.resources/'v001',self.resources/'depth map'/'latest',self.resources/'depth map'/'v001'/'child']:
            with self.assertRaises(ValueError):validate_job({**self.job,'run_dir':str(bad)})
    def test_foreign_binding_rejected(self):
        with self.assertRaises(ValueError):validate_job({**self.job,'binding':{'resources_dir':str(self.root/'Resources')}})
    def test_stage_cache_detects_corrupt_output_and_parameter_change(self):
        ctx=Context(self.job);artifact=ctx.run/'frame.png';artifact.write_bytes(b'original')
        ctx.finish_stage('decode','a',[artifact],{'frame_count':1})
        self.assertIsNotNone(ctx.cache_record('decode','a'))
        self.assertIsNone(ctx.cache_record('decode','b'))
        artifact.write_bytes(b'changed!')
        self.assertIsNone(ctx.cache_record('decode','a'))
    def test_cancellation_interrupts_validation(self):
        ctx=Context(self.job);(ctx.run/'cancel.flag').touch()
        with self.assertRaises(Cancelled):ctx.cancel()
        with self.assertRaises(Cancelled):ctx.finish_stage('decode','a',[self.source],{})
    def test_atomic_json_and_lock_exclusion(self):
        ctx=Context(self.job);path=ctx.run/'x.json';atomic_json(path,{'a':[1,2]})
        self.assertEqual(read_json(path),{'a':[1,2]})
        self.assertFalse(list(ctx.run.glob('*.tmp')))
        lock=acquire_lock(ctx)
        with self.assertRaises(RuntimeError):acquire_lock(ctx)
        lock.unlink()
    def test_settings_are_bounded(self):
        for settings in [{'cleanup_strength':1.1},{'edge_radius':-1},{'subject_id':-1},{'matte_warmup':0},{'working_max_width':False}]:
            with self.assertRaises(ValueError):validate_job({**self.job,'settings':settings})
    def test_atomic_replace_retries_transient_windows_reader_lock(self):
        path=self.root/'progress.json';real_replace=os.replace;calls=[]
        def flaky(a,b):
            calls.append(1)
            if len(calls)<3:raise PermissionError('simulated Windows reader sharing violation')
            return real_replace(a,b)
        with patch('common.os.replace',side_effect=flaky):atomic_json(path,{'status':'cancelled'})
        self.assertEqual(len(calls),3)
        self.assertEqual(read_json(path)['status'],'cancelled')
    def linked_job(self):
        shot_id=str(uuid.uuid4());project_id=str(uuid.uuid4());path=self.shot/'shot.json'
        atomic_json(path,{'schema':'blenderae.shot','schema_version':1,'shot_id':shot_id,'project_id':project_id})
        return {**self.job,'binding':{**self.job['binding'],'binding_kind':'blenderae.shot','manifest_path':str(path),
                                      'shot_id':shot_id,'project_id':project_id}}
    def test_linked_job_rejects_wrong_ids_and_manifest_path(self):
        job=self.linked_job();self.assertEqual(validate_job(job)['binding']['shot_id'],job['binding']['shot_id'])
        for field in ['shot_id','project_id']:
            with self.assertRaisesRegex(ValueError,field):validate_job({**job,'binding':{**job['binding'],field:str(uuid.uuid4())}})
        with self.assertRaisesRegex(ValueError,'manifest_path'):
            validate_job({**job,'binding':{**job['binding'],'manifest_path':str(self.root/'shot.json')}})
        with self.assertRaisesRegex(ValueError,'manifest_path'):validate_job(self.job)
    def test_changed_linked_manifest_blocks_final_publication(self):
        from process import validate_and_finish
        job=self.linked_job();ctx=Context(job);path=self.shot/'shot.json';current=read_json(path)
        current['project_id']=str(uuid.uuid4());atomic_json(path,current)
        with self.assertRaisesRegex(ValueError,'project_id'):
            validate_and_finish(ctx,{},'unused','unused',0)
        self.assertFalse((ctx.run/'manifest.json').exists())
    def test_malformed_nested_fields_fail_actionably(self):
        for field in ['settings','binding','runtime']:
            with self.assertRaisesRegex(ValueError,field):validate_job({**self.job,field:[]})
        for points in [['bad'],[{'positive':'bad'}],[{'positive':[[float('nan'),0]]}],[{'frame':-1,'positive':[[1,2]]}]]:
            with self.assertRaises(ValueError):validate_job({**self.job,'settings':{'subject_points':points}})

if __name__=='__main__':unittest.main()

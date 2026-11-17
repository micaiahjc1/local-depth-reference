"""Supplied-matte pixels, validation, cache routing and native-format export tests."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import Context, Cancelled, atomic_json, digest, file_hash, read_json, validate_job
import process
import supplied_matte as supplied
import ae_export


class SuppliedMatteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.shot = self.root/'Shot'; self.shot.mkdir()
        self.source = self.root/'original.mov'; self.source.write_bytes(b'original source fixture')
        self.inputs = self.root/'mattes'; self.inputs.mkdir()
        self.w, self.h, self.n = 64, 48, 4
        self.images = []
        for i in range(self.n):
            a = np.zeros((self.h, self.w), np.uint16)
            a[5:43, 8:56] = 65535
            a[4, 8:56] = np.arange(48, dtype=np.uint16)*1000 + i
            self.images.append(a)
            self.write(i, a)
        self.job = {'source':str(self.source), 'shot_dir':str(self.shot),
                    'run_dir':str(self.shot/'Resources/depth map/v001'),
                    'binding':{'resources_dir':str(self.shot/'Resources')}, 'runtime':{},
                    'settings':{'matte_source':'custom', 'matte_path':str(self.inputs/'artist_01001.png')}}
        self.ctx = Context(self.job)
        self.src = {'path':str(self.source), 'sha256':file_hash(self.source),
                    'width':self.w, 'height':self.h, 'working_width':self.w, 'working_height':self.h,
                    'frame_count':self.n, 'fps_num':24000, 'fps_den':1001, 'pixel_aspect':1}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, i, image, suffix='.png'):
        path = self.inputs/f'artist_{1001+i:05d}{suffix}'
        self.assertTrue(cv2.imwrite(str(path), image))
        return path

    def inspect(self):
        return supplied.inspect_sequence(self.ctx, self.src)

    def import_matte(self):
        snapshot = self.inspect()
        return supplied.import_sequence(self.ctx, digest(snapshot), snapshot)

    def test_16bit_window_preserved_exactly_and_inputs_unchanged(self):
        self.write(-1, self.images[0]); self.write(self.n, self.images[0])
        before = {p:file_hash(p) for p in self.inputs.iterdir()}
        row = self.import_matte()
        self.assertEqual(row['data']['first_file_index'], 1001)
        self.assertEqual(row['data']['frames'], self.n)
        self.assertFalse(row['data']['models_used'])
        self.assertEqual(row['data']['resampling'], 'none')
        for i, expected in enumerate(self.images):
            actual = cv2.imread(str(self.ctx.run/'matte'/f'{i:05d}.png'), -1)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(actual.dtype, np.uint16)
        self.assertEqual(before, {p:file_hash(p) for p in self.inputs.iterdir()})

    def test_rgba_explicit_alpha_ignores_rgb_and_luma_ignores_alpha(self):
        rgba = np.zeros((self.h, self.w, 4), np.uint16)
        rgba[:,:,:3] = [1234, 20001, 60003]
        rgba[:,:,3] = self.images[0]
        for i in range(self.n): self.write(i, rgba)
        self.ctx.settings['matte_channel'] = 'alpha'
        self.import_matte()
        np.testing.assert_array_equal(cv2.imread(str(self.ctx.run/'matte/00000.png'), -1), rgba[:,:,3])
        self.ctx.settings['matte_channel'] = 'luma'
        self.import_matte()
        expected = round(60003*.2126 + 20001*.7152 + 1234*.0722)
        self.assertTrue(np.all(cv2.imread(str(self.ctx.run/'matte/00000.png'), -1) == expected))

    def test_invert_preserves_16bit_steps(self):
        self.ctx.settings['matte_invert'] = True
        self.import_matte()
        np.testing.assert_array_equal(cv2.imread(str(self.ctx.run/'matte/00000.png'), -1), 65535-self.images[0])

    def test_8bit_tiff_supported_without_synthetic_precision(self):
        for i in range(self.n): self.write(i, (self.images[i]//257).astype(np.uint8), '.tif')
        self.ctx.settings['matte_path'] = str(self.inputs/'artist_01001.tif')
        row = self.import_matte()
        self.assertEqual(row['data']['output_dtype'], 'uint8')
        np.testing.assert_array_equal(cv2.imread(str(self.ctx.run/'matte/00000.png'), -1), (self.images[0]//257).astype(np.uint8))

    def test_aspect_matched_downscale_retains_u16_and_recorded_interpolation(self):
        for i in range(self.n): self.write(i, cv2.resize(self.images[i], (self.w*2, self.h*2), interpolation=cv2.INTER_NEAREST))
        row = self.import_matte()
        self.assertEqual(row['data']['resampling'], 'area')
        np.testing.assert_array_equal(cv2.imread(str(self.ctx.run/'matte/00000.png'), -1), self.images[0])

    def test_working_size_is_accepted_with_even_pixel_decode_rounding(self):
        source = {**self.src, 'width':96, 'height':74}
        # The exact declared working dimensions remain authoritative.
        row = supplied.inspect_sequence(self.ctx, source)
        self.assertEqual(row['output_resolution'], [64,48])

    def test_missing_frame_and_too_short_window_fail(self):
        for index in [1, self.n-1]:
            with self.subTest(index=index):
                path = self.inputs/f'artist_{1001+index:05d}.png'; saved = path.read_bytes(); path.unlink()
                with self.assertRaisesRegex(ValueError, 'missing source frame'): self.inspect()
                path.write_bytes(saved)

    def test_wrong_aspect_mixed_size_precision_channel_and_corrupt_frame_fail(self):
        for kind in ['aspect','size','precision','channel','corrupt']:
            with self.subTest(kind=kind):
                for i, a in enumerate(self.images): self.write(i,a)
                if kind=='aspect':
                    for i in range(self.n):self.write(i,np.ones((100,64),np.uint16))
                elif kind=='size':self.write(1,np.ones((self.h,self.w+1),np.uint16))
                elif kind=='precision':self.write(1,(self.images[1]//257).astype(np.uint8))
                elif kind=='channel':self.write(1,np.repeat(self.images[1][:,:,None],3,axis=2))
                else:(self.inputs/'artist_01002.png').write_bytes(b'not an image')
                with self.assertRaises(ValueError):self.inspect()

    def test_alpha_requires_alpha_channel_float_tiff_rejected(self):
        self.ctx.settings['matte_channel']='alpha'
        with self.assertRaisesRegex(ValueError, 'no alpha channel'):self.inspect()
        self.ctx.settings['matte_channel']='luma'
        for i in range(self.n):self.write(i,self.images[i].astype(np.float32)/65535,'.tiff')
        self.ctx.settings['matte_path']=str(self.inputs/'artist_01001.tiff')
        with self.assertRaisesRegex(ValueError, 'unsigned 8- or 16-bit'):self.inspect()

    def test_invalid_settings_and_self_owned_input_rejected(self):
        for setting in [{'matte_source':'guess'},{'matte_channel':'auto'},{'matte_invert':1},{'matte_path':''}]:
            with self.assertRaises((ValueError,FileNotFoundError)):
                validate_job({**self.job,'settings':{**self.job['settings'],**setting}})
        path=self.ctx.run/'own_00000.png';cv2.imwrite(str(path),self.images[0])
        with self.assertRaisesRegex(ValueError,'outside'):
            validate_job({**self.job,'settings':{**self.job['settings'],'matte_path':str(path)}})

    def test_change_between_inspection_and_import_is_rejected(self):
        snapshot=self.inspect();changed=self.images[1].copy();changed[0,0]=100;self.write(1,changed)
        with self.assertRaisesRegex(ValueError,'changed'):supplied.import_sequence(self.ctx,digest(snapshot),snapshot)
        self.assertFalse((self.ctx.cache/'matte.json').exists())

    def test_changed_inputs_get_new_cache_key_and_reimport(self):
        first=self.import_matte();changed=self.images[1].copy();changed[0,0]=12345;self.write(1,changed)
        snapshot=self.inspect();key=digest(snapshot)
        self.assertNotEqual(first['key'],key)
        self.assertIsNone(self.ctx.cache_record('matte',key))
        supplied.import_sequence(self.ctx,key,snapshot)
        np.testing.assert_array_equal(cv2.imread(str(self.ctx.run/'matte/00001.png'),-1),changed)

    def test_cancellation_during_import_does_not_commit_stage(self):
        snapshot=self.inspect();original=self.ctx.progress
        def cancel(stage,fraction,message,**kwargs):
            original(stage,fraction,message,**kwargs)
            if fraction>.2:(self.ctx.run/'cancel.flag').touch()
        self.ctx.progress=cancel
        with self.assertRaises(Cancelled):supplied.import_sequence(self.ctx,digest(snapshot),snapshot)
        self.assertFalse((self.ctx.cache/'matte.json').exists())
        (self.ctx.run/'cancel.flag').unlink();self.ctx.progress=original
        supplied.import_sequence(self.ctx,digest(snapshot),snapshot)
        self.assertIsNotNone(self.ctx.cache_record('matte',digest(snapshot)))

    def make_decode(self, ctx, key):
        files=[]
        for i in range(self.n):
            p=ctx.run/'source'/f'{i:05d}.png';p.parent.mkdir(exist_ok=True)
            cv2.imwrite(str(p),np.full((self.h,self.w,3),80+i,np.uint8));files.append(p)
        return ctx.finish_stage('decode',key,files,self.src)

    def fake_depth(self, ctx, stage, key):
        self.assertEqual(stage,'depth','Custom pipeline must never launch a tracking or matte model')
        target=ctx.run/'depth/relative_raw.npy';target.parent.mkdir(exist_ok=True)
        q=np.broadcast_to(np.linspace(1,4,self.w,dtype=np.float32)[None,None,:],(self.n,self.h,self.w)).copy()
        np.save(target,q)
        return ctx.finish_stage('depth',key,[target],{'fixture':True})

    def model_free_runtime(self):
        model=self.root/'depth_checkpoint.pth';model.write_bytes(b'fixture checkpoint')
        repo=self.root/'vda';repo.mkdir(exist_ok=True);(repo/'fake.py').write_text('# fixture')
        self.ctx.runtime.update(vda_checkpoint=str(model),vda_repo=str(repo))

    def test_custom_pipeline_no_matting_dependencies_and_u16_blender_ae_exports(self):
        self.model_free_runtime()
        with patch.object(process,'decode',side_effect=self.make_decode),patch.object(process,'run_child',side_effect=self.fake_depth) as child:
            process.pipeline(self.ctx)
        self.assertEqual(child.call_count,1)
        base=read_json(self.ctx.run/'manifest.json')
        self.assertEqual(base['processing']['matte_source'],'custom')
        self.assertEqual(base['processing']['alpha_bit_depth'],16)
        self.assertIsNone(base['processing']['tracking_inference'])
        self.assertIsNone(base['processing']['beeble_data_used'])
        self.assertNotIn('tracking',base['stage_cache'])
        for i, expected in enumerate(self.images):
            np.testing.assert_array_equal(cv2.imread(str(self.ctx.run/'alpha'/f'{i:05d}.png'),-1),expected)
        self.assertEqual(base['processing']['normalization']['max_core_change'],0)
        # Export remains self-contained when supplied source files are offline.
        for p in self.inputs.iterdir():p.unlink()
        ae_job={'schema':1,'kind':'ae_export','source_manifest':str(self.ctx.run/'manifest.json'),
                'shot_dir':str(self.shot),'run_dir':str(self.shot/'Resources/depth map/v002'),
                'binding':base['binding'],'runtime':{},'settings':{'ae_coverage':'both'}}
        result=ae_export.export_job(ae_job)
        self.assertEqual(result['processing']['matte_source'],'custom')
        self.assertIn('retained exactly',result['encoding']['alpha_conversion'])
        for i, expected in enumerate(self.images):
            np.testing.assert_array_equal(cv2.imread(str(Path(ae_job['run_dir'])/'ae/alpha'/f'{i:05d}.png'),-1),expected)

    def test_custom_change_before_publication_rejected_then_resume_reuses_depth(self):
        self.model_free_runtime();real_finish=process.validate_and_finish
        def altered(ctx,*args):
            image=self.images[1].copy();image[4,9]=12345;self.write(1,image)
            return real_finish(ctx,*args)
        with patch.object(process,'decode',side_effect=self.make_decode),patch.object(process,'run_child',side_effect=self.fake_depth),patch.object(process,'validate_and_finish',side_effect=altered):
            with self.assertRaisesRegex(ValueError,'Supplied matte changed'):process.pipeline(self.ctx)
        self.assertFalse((self.ctx.run/'manifest.json').exists())
        with patch.object(process,'decode',side_effect=AssertionError('Decode should be cached')),patch.object(process,'run_child',side_effect=AssertionError('Depth should be cached')):
            process.pipeline(self.ctx)
        self.assertEqual(cv2.imread(str(self.ctx.run/'alpha/00001.png'),-1)[4,9],12345)

    def test_u8_and_u16_equivalent_cleanup_has_identical_depth(self):
        import OpenEXR
        atomic_json(self.ctx.cache/'decode.json',{'data':self.src})
        (self.ctx.run/'depth').mkdir(exist_ok=True)
        q=np.broadcast_to(np.linspace(1,4,self.w,dtype=np.float32)[None,None,:],(self.n,self.h,self.w)).copy()
        np.save(self.ctx.run/'depth/relative_raw.npy',q)
        (self.ctx.run/'matte').mkdir(exist_ok=True)
        def outputs():
            found=[]
            for kind in ['raw_z','clean_z']:
                for i in range(self.n):
                    with OpenEXR.File(str(self.ctx.run/'depth'/kind/f'{i:05d}.exr')) as f:found.append(f.channels()['RGB'].pixels.copy())
            return found
        for i,a in enumerate(self.images):cv2.imwrite(str(self.ctx.run/'matte'/f'{i:05d}.png'),(a//257).astype(np.uint8))
        eight=process.export_depth(self.ctx,'eight');original=outputs()
        for i,a in enumerate(self.images):cv2.imwrite(str(self.ctx.run/'matte'/f'{i:05d}.png'),(a//257*257).astype(np.uint16))
        sixteen=process.export_depth(self.ctx,'sixteen');current=outputs()
        self.assertEqual(eight['data']['q_median'],sixteen['data']['q_median'])
        for old,new in zip(original,current):np.testing.assert_array_equal(old,new)

    def test_automatic_cleanup_output_bytes_match_packaged_021(self):
        import types
        import zipfile
        baseline=Path(__file__).resolve().parents[2]/'dist/local-depth-reference-0.2.1-blender-4.2.zip'
        if not baseline.is_file():self.skipTest('Packaged0.2.1 baseline unavailable')
        with zipfile.ZipFile(baseline) as archive:code=archive.read('local_depth_reference/worker/process.py').decode('utf-8')
        legacy=types.ModuleType('legacy_process_021');legacy.__file__=str(baseline)
        exec(compile(code,str(baseline),'exec'),legacy.__dict__)
        self.ctx.settings['matte_source']='automatic'
        atomic_json(self.ctx.cache/'decode.json',{'data':self.src})
        (self.ctx.run/'depth').mkdir(exist_ok=True);(self.ctx.run/'matte').mkdir(exist_ok=True)
        q=np.broadcast_to(np.linspace(1,4,self.w,dtype=np.float32)[None,None,:],(self.n,self.h,self.w)).copy()
        np.save(self.ctx.run/'depth/relative_raw.npy',q)
        for i,a in enumerate(self.images):cv2.imwrite(str(self.ctx.run/'matte'/f'{i:05d}.png'),(a//257).astype(np.uint8))
        before=legacy.export_depth(self.ctx,'legacy')
        pixels={item['path']:file_hash(self.ctx.run/item['path']) for item in before['artifacts']}
        after=process.export_depth(self.ctx,'current')
        self.assertEqual(pixels,{item['path']:file_hash(self.ctx.run/item['path']) for item in after['artifacts']})
        for key,value in before['data'].items():self.assertEqual(value,after['data'][key])

    def test_all_soft_or_empty_custom_mattes_fail_before_publish(self):
        atomic_json(self.ctx.cache/'decode.json',{'data':self.src})
        (self.ctx.run/'depth').mkdir(exist_ok=True);(self.ctx.run/'matte').mkdir(exist_ok=True)
        np.save(self.ctx.run/'depth/relative_raw.npy',np.ones((self.n,self.h,self.w),np.float32))
        for value in [0,60000]:
            with self.subTest(value=value):
                for i in range(self.n):cv2.imwrite(str(self.ctx.run/'matte'/f'{i:05d}.png'),np.full((self.h,self.w),value,np.uint16))
                with self.assertRaisesRegex(ValueError,'supplied mattes need opaque foreground'):
                    process.export_depth(self.ctx,'invalid')
                self.assertFalse((self.ctx.run/'manifest.json').exists())

    def test_automatic_pipeline_keeps_tracking_selection_and_refinement_route(self):
        self.model_free_runtime();self.ctx.settings['matte_source']='automatic'
        self.ctx.job['settings']['matte_source']='automatic'
        for name in ['sam3_checkpoint','matanyone_checkpoint']:
            path=self.root/(name+'.pth');path.write_bytes(name.encode());self.ctx.runtime[name]=str(path)
        for name in ['sam3_module_root','matanyone_root']:
            path=self.root/name;path.mkdir();(path/'model.py').write_text('# fixture');self.ctx.runtime[name]=str(path)
        seen=[]
        def models(ctx,stage,key):
            seen.append(stage)
            if stage=='tracking':
                return ctx.finish_stage(stage,key,[],{'candidates':[{'id':7,'credible':True,'nonempty_frames':self.n}]})
            if stage=='matte':
                self.assertEqual(read_json(ctx.cache/'selected_subject.json')['id'],7)
                folder=ctx.run/'matte';folder.mkdir(exist_ok=True);files=[]
                for i,a in enumerate(self.images):
                    path=folder/f'{i:05d}.png';cv2.imwrite(str(path),(a//257).astype(np.uint8));files.append(path)
                return ctx.finish_stage(stage,key,files,{'automatic_model_fixture':True})
            return self.fake_depth(ctx,stage,key)
        with patch.object(process,'decode',side_effect=self.make_decode),patch.object(process,'run_child',side_effect=models):process.pipeline(self.ctx)
        self.assertEqual(seen,['tracking','matte','depth'])
        result=read_json(self.ctx.run/'manifest.json')
        self.assertEqual(result['processing']['matte_model'],'SAM3 + MatAnyone2')
        self.assertEqual(result['processing']['selected_subject']['id'],7)
        self.assertEqual(result['processing']['alpha_bit_depth'],8)


if __name__=='__main__':unittest.main()

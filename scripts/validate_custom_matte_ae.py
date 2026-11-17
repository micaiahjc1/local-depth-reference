"""Verify native AE image-sequence import, frame correspondence and separate coverage."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np

p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
out=a.output.resolve();build=json.loads((out/'build_report.json').read_text(encoding='utf-8-sig'))
assert build['status']=='ready'
report={'status':'running','checks':[],'native_bpc':32}
for stream in build['queue']:
    for index in range(stream['count']):
        source=Path(stream['source']).with_name(f'{index:05d}.png')
        src=cv2.imread(str(source),cv2.IMREAD_UNCHANGED)
        render=out/'renders'/f'{stream["id"]}_{index:05d}.png'
        actual=cv2.imread(str(render),cv2.IMREAD_UNCHANGED)
        if actual is None or actual.dtype!=np.uint16 or actual.shape!=(stream['height'],stream['width'],4):
            raise AssertionError(f'Bad native output: {render}')
        # AE converts16-bit integer images to its32768-step channel convention
        # before rendering. This is verified separately in the depth-controller QA.
        expected=np.rint(src.astype(np.float64)/65535*32768)/32768
        rgb=actual[:,:,:3].astype(np.float64)/65535
        alpha=actual[:,:,3].astype(np.float64)/65535
        selected=np.ones(src.shape,dtype=bool)
        if stream['alpha']:
            matte=Path(stream['alpha']).with_name(f'{index:05d}.png')
            raw_a=cv2.imread(str(matte),cv2.IMREAD_UNCHANGED)
            expected_a=np.rint(raw_a.astype(np.float64)/65535*32768)/32768
            selected=expected_a>0
            alpha_error=float(np.max(np.abs(alpha-expected_a)))*65535
            assert np.all(actual[:,:,3][raw_a==0]==0),'Transparent background leak'
        else:
            alpha_error=float(np.max(np.abs(alpha-1)))*65535
        rgb_error=float(np.max(np.abs(rgb[selected]-expected[selected,None]))) *65535 if selected.any() else 0
        assert rgb_error<=4 and alpha_error<=2,(stream['id'],index,rgb_error,alpha_error)
        report['checks'].append({'stream':stream['id'],'frame':index,'max_rgb_error_lsb':rgb_error,'max_alpha_error_lsb':alpha_error,'passed':True})
report.update(status='passed',frames_checked=len(report['checks']),source_modes=[s['label'] for s in build['sources']])
(out/'native_validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='checks'}))

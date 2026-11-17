"""Generate inspectable contact sheets outside a completed immutable version."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np

parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--output',required=True)
args=parser.parse_args();path=Path(args.manifest).resolve();run=path.parent;out=Path(args.output).resolve()
assert not out.is_relative_to(run),'Review output must not modify the completed run'
out.mkdir(parents=True,exist_ok=True);manifest=json.loads(path.read_text());n=manifest['sequence']['count']
w=manifest['processing']['working_width'];h=manifest['processing']['working_height'];tw=320;th=round(tw*h/w)
yy,xx=np.indices((h,w));board=np.where(((xx//32+yy//32)%2)==0,70,110).astype(np.float32)
tiles=[]
for i in range(n):
    rgb=cv2.imread(str(run/'source'/f'{i:05d}.png'));a=cv2.imread(str(run/'alpha'/f'{i:05d}.png'),0).astype(np.float32)/255
    comp=(rgb*a[:,:,None]+board[:,:,None]*(1-a[:,:,None])).astype(np.uint8)
    tile=np.full((th+24,tw,3),20,np.uint8)
    tile[:th]=cv2.resize(comp,(tw,th),interpolation=cv2.INTER_AREA)
    cv2.putText(tile,f'Frame {i+1}',(8,th+17),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,255),1,cv2.LINE_AA)
    tiles.append(tile)
for page,start in enumerate(range(0,n,49)):
    canvas=np.zeros(((th+24)*7,tw*7,3),np.uint8)
    for j,tile in enumerate(tiles[start:start+49]):canvas[j//7*(th+24):(j//7+1)*(th+24),j%7*tw:(j%7+1)*tw]=tile
    cv2.imwrite(str(out/f'all_frames_{page+1:02d}.jpg'),canvas,[cv2.IMWRITE_JPEG_QUALITY,93])
indices=sorted(set([0,n//6,n//3,n//2,2*n//3,5*n//6,n-1]))
summary=np.zeros((len(indices)*(th+24),tw*3,3),np.uint8)
for row,i in enumerate(indices):
    rgb=cv2.imread(str(run/'source'/f'{i:05d}.png'));a=cv2.imread(str(run/'alpha'/f'{i:05d}.png'),0)
    for col,im in enumerate([rgb,cv2.cvtColor(a,cv2.COLOR_GRAY2BGR)]):summary[row*(th+24):row*(th+24)+th,col*tw:(col+1)*tw]=cv2.resize(im,(tw,th))
    summary[row*(th+24):(row+1)*(th+24),2*tw:]=tiles[i]
cv2.imwrite(str(out/'summary.jpg'),summary,[cv2.IMWRITE_JPEG_QUALITY,96])
print(out)

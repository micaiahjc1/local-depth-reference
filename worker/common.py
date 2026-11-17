"""Filesystem, job validation, progress and resumable stage records (stdlib only)."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

SCHEMA = 1
DEFAULTS = {
    'working_max_width':1280, 'vda_input_size':518, 'subject_prompt':'person',
    'subject_id':None, 'subject_points':[], 'matte_short_side':1400,
    'matte_warmup':5, 'matte_reset_interval':8, 'edge_radius':2,
    'edge_inset':0, 'cleanup_strength':1.0,
    'matte_source':'automatic', 'matte_path':'', 'matte_channel':'luma', 'matte_invert':False,
}
STAGE_RANGES = {'decode':(0,.12),'tracking':(.12,.32),'selection':(.32,.33),
                'matte':(.33,.58),'depth':(.58,.80),'export':(.80,.96),'validation':(.96,1)}

class Cancelled(Exception):
    pass

class AwaitingSubject(Exception):
    pass

def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8',newline='\n') as stream:
            json.dump(data,stream,indent=2,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        # Windows readers and virus scanners may momentarily open the old file
        # without FILE_SHARE_DELETE. Retry replacement; never fall back to an
        # in-place/truncated write that a polling Blender UI could misread.
        for attempt in range(10):
            try:os.replace(temp,path);break
            except PermissionError:
                if attempt==9:raise
                time.sleep(min(.01*(attempt+1),.08))
    finally:
        temp.unlink(missing_ok=True)

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def file_hash(path, cancel=None):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):
            if cancel:cancel()
            digest.update(chunk)
    return digest.hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def code_hash():
    return digest({p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))})

def tree_hash(root):
    root=Path(root)
    return digest({str(p.relative_to(root)):file_hash(p) for p in sorted(root.rglob('*.py'))
                   if '.git' not in p.parts and '__pycache__' not in p.parts})

def pid_alive(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,int(pid))
        if not handle:return ctypes.get_last_error()==5
        try:
            status=wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(status))) and status.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(int(pid),0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def acquire_lock(ctx):
    path=ctx.cache/'worker.lock'
    for attempt in range(2):
        try:
            fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump({'pid':os.getpid(),'created_unix':time.time()},stream)
            return path
        except FileExistsError:
            try:row=read_json(path)
            except (OSError,ValueError):raise RuntimeError('Another worker is starting or the worker lock is unreadable')
            if pid_alive(row.get('pid',0)):raise RuntimeError('Another worker is already processing this version')
            if attempt:raise RuntimeError('Could not acquire worker lock')
            path.unlink()

def validate_job(job):
    if not isinstance(job,dict):raise ValueError('Job must be a JSON object')
    job=dict(job)
    if job.get('schema',1)!=1:raise ValueError('Unsupported job schema')
    source=Path(job['source']).expanduser().resolve(strict=True)
    if not source.is_file():raise ValueError('Source must be a video file')
    shot=Path(job['shot_dir']).expanduser().resolve(strict=True)
    if not shot.is_dir():raise ValueError('shot_dir must be an existing shot directory')
    if not isinstance(job.get('binding',{}),dict):raise ValueError('binding must be a JSON object')
    binding=dict(job.get('binding',{}))
    if binding.get('shot_dir') and Path(binding['shot_dir']).expanduser().resolve()!=shot:
        raise ValueError('binding.shot_dir disagrees with job shot_dir')
    manifest=shot/'shot.json'
    linked=manifest.exists() or binding.get('binding_kind')=='blenderae.shot' or any(binding.get(k) for k in ['shot_id','project_id','manifest_path'])
    if linked:
        bound_path=binding.get('manifest_path')
        if not isinstance(bound_path,str) or not Path(bound_path).is_absolute() or Path(bound_path).resolve()!=manifest.resolve():
            raise ValueError('Linked binding manifest_path must be the exact absolute shot_dir/shot.json')
        if not manifest.is_file():raise ValueError('Linked shot.json is missing')
        current=read_json(manifest)
        if not isinstance(current,dict) or current.get('schema')!='blenderae.shot' or current.get('schema_version')!=1:
            raise ValueError('Linked shot.json has unsupported schema/version')
        for field in ['shot_id','project_id']:
            try:
                actual=uuid.UUID(current.get(field,''));expected=uuid.UUID(binding.get(field,''))
            except (ValueError,AttributeError,TypeError) as exc:
                raise ValueError(f'Linked {field} must be a valid UUID in both binding and shot.json') from exc
            if actual!=expected:raise ValueError(f'Linked {field} disagrees with current shot.json')
        binding['binding_kind']='blenderae.shot'
    resources=Path(binding.get('resources_dir',shot/'resources')).expanduser().resolve()
    if resources.parent!=shot or resources.name.casefold()!='resources':
        raise ValueError('Bound resources folder must be directly inside the selected shot')
    depth_root=Path(binding.get('depth_root',resources/'depth map')).expanduser().resolve()
    if depth_root.parent!=resources or depth_root.name.casefold()!='depth map':
        raise ValueError('Depth root must be the shot resources/depth map folder')
    run=Path(job['run_dir']).expanduser().resolve()
    if run.parent!=depth_root or not re.fullmatch(r'v\d{3,}',run.name):
        raise ValueError('run_dir must be a version vNNN directly below this shot resources/depth map')
    # Resolving every ancestor above prevents symlinks/junctions escaping the shot.
    if not run.is_relative_to(shot):raise ValueError('Run path escapes the bound shot')
    binding.update(shot_dir=str(shot),resources_dir=str(resources),depth_root=str(depth_root))
    if not isinstance(job.get('settings',{}),dict):raise ValueError('settings must be a JSON object')
    settings={**DEFAULTS,**job.get('settings',{})}
    if settings['matte_source'] not in ('automatic','custom'):
        raise ValueError('matte_source must be automatic or custom')
    if settings['matte_channel'] not in ('luma','alpha'):
        raise ValueError('matte_channel must be luma or alpha')
    if not isinstance(settings['matte_invert'],bool):
        raise ValueError('matte_invert must be a boolean')
    if not isinstance(settings['matte_path'],str):
        raise ValueError('matte_path must be a path string')
    if settings['matte_source']=='custom':
        if not settings['matte_path'].strip():raise ValueError('Choose the first frame of your supplied matte sequence')
        matte=Path(settings['matte_path']).expanduser().resolve(strict=True)
        if not matte.is_file() or matte.suffix.lower() not in ('.png','.tif','.tiff') or not re.fullmatch(r'.*\d+\.[^.]+',matte.name):
            raise ValueError('Choose a numbered PNG or TIFF matte sequence')
        if matte.is_relative_to(run):raise ValueError('Supplied matte must be outside its output run')
        settings['matte_path']=str(matte)
    for key,lo,hi in [('working_max_width',64,8192),('vda_input_size',14,2048),
                      ('matte_short_side',64,8192),('matte_warmup',1,30),
                      ('matte_reset_interval',1,120),('edge_radius',0,64),('edge_inset',0,64)]:
        value=settings[key]
        if isinstance(value,bool) or int(value)!=value or not lo<=int(value)<=hi:
            raise ValueError(f'{key} must be an integer in [{lo}, {hi}]')
        settings[key]=int(value)
    strength=float(settings['cleanup_strength'])
    if not 0<=strength<=1:raise ValueError('cleanup_strength must be in [0, 1]')
    settings['cleanup_strength']=strength
    if not isinstance(settings['subject_prompt'],str) or not settings['subject_prompt'].strip():
        raise ValueError('subject_prompt must contain text')
    sid=settings['subject_id']
    if sid is not None and (isinstance(sid,bool) or not isinstance(sid,int) or sid<0):
        raise ValueError('subject_id must be null or a nonnegative integer')
    if not isinstance(settings['subject_points'],list):raise ValueError('subject_points must be a list')
    for point in settings['subject_points']:
        if not isinstance(point,dict):raise ValueError('Each subject_points item must be an object')
        if not isinstance(point.get('frame',0),int) or isinstance(point.get('frame',0),bool) or point.get('frame',0)<0:
            raise ValueError('Point frame must be a nonnegative integer')
        if not isinstance(point.get('positive'),list) or not point['positive']:
            raise ValueError('Each point prompt needs a nonempty positive list')
        if not isinstance(point.get('negative',[]),list):raise ValueError('Point negative entries must be a list')
        for xy in point['positive']+point.get('negative',[]):
            if not isinstance(xy,(list,tuple)) or len(xy)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in xy):
                raise ValueError('Point coordinates must be finite nonnegative [x, y] numeric pairs')
    job.update(source=str(source),shot_dir=str(shot),run_dir=str(run),binding=binding,settings=settings)
    if not isinstance(job.get('runtime'),dict):raise ValueError('Missing runtime configuration')
    return job

class Context:
    def __init__(self, job):
        self.job=validate_job(job);self.run=Path(self.job['run_dir'])
        self.settings=self.job['settings'];self.runtime=self.job['runtime']
        self.run.mkdir(parents=True,exist_ok=True)
        self.cache=self.run/'_cache';self.cache.mkdir(exist_ok=True)
    def cancel(self):
        if (self.run/'cancel.flag').exists():raise Cancelled('Cancelled by user')
    def progress(self,stage,fraction,message,status='running',**extra):
        lo,hi=STAGE_RANGES.get(stage,(0,1))
        atomic_json(self.run/'progress.json',{'schema':1,'status':status,'stage':stage,
            'stage_fraction':max(0,min(1,float(fraction))),
            'fraction':lo+(hi-lo)*max(0,min(1,float(fraction))),
            'message':message,'updated_unix':time.time(),'pid':os.getpid(),**extra})
    def relative(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.run):raise ValueError('Worker output escapes run directory')
        return path.relative_to(self.run).as_posix()
    def cache_record(self,stage,key):
        path=self.cache/(stage+'.json')
        if not path.exists():return None
        try:
            row=read_json(path)
            if row['key']!=key:return None
            for item in row['artifacts']:
                self.cancel();p=(self.run/item['path']).resolve()
                if not p.is_relative_to(self.run) or not p.is_file() or p.stat().st_size!=item['bytes'] or file_hash(p,self.cancel)!=item['sha256']:
                    return None
            return row
        except (KeyError,ValueError,OSError,json.JSONDecodeError):return None
    def finish_stage(self,stage,key,files,data):
        artifacts=[]
        for path in files:
            self.cancel();path=Path(path)
            artifacts.append({'path':self.relative(path),'bytes':path.stat().st_size,'sha256':file_hash(path,self.cancel)})
        row={'schema':1,'key':key,'stage':stage,'data':data,'artifacts':artifacts,'completed_unix':time.time()}
        atomic_json(self.cache/(stage+'.json'),row)
        self.progress(stage,1,f'{stage.capitalize()} complete')
        return row

def sequence_files(folder,count,suffix):
    return [Path(folder)/f'{i:05d}{suffix}' for i in range(count)]

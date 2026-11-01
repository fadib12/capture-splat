"""Bounded, resumable transfer of finalized capture assets to the private PC."""
from __future__ import annotations
import base64
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
from itertools import islice
from .pc_bridge import BridgeStore, _bounded_file, _bounded_digest, strict_json

CHUNK_BYTES = 4 * 1024**2
MAX_ASSET_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_FILES = 4096
REVISION = 'gsplat-rgbd-detail-v2'
ROOT_FILES = {'capture.json', 'imu.csv', 'gps.csv'}
ROOT_DIRS = {'rgb','depth','confidence','masks','metadata','geometry','video','room_plan','pointcloud_preview'}


def validate_descriptor(body: bytes):
    data = strict_json(body)
    if data.get('schema') != 'capture_splat.full_transfer.v1':
        raise ValueError('Unsupported full capture descriptor.')
    manifest = base64.b64decode(data['manifest_base64'], validate=True)
    if not 0 < len(manifest) <= 16 * 1024**2:
        raise ValueError('Invalid manifest size.')
    from .pc_bridge import image_inventory
    capture = strict_json(manifest)
    image_inventory(capture)
    files = data['files']
    if not isinstance(files,list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Asset inventory exceeds limits.')
    seen, total = set(), 0
    for binding in files:
        name = binding['path']
        if not isinstance(name,str) or len(name)>240 or not re.fullmatch(r'[A-Za-z0-9_.\-/]+',name):
            raise ValueError('Nonportable asset path.')
        path = PurePosixPath(name)
        if (path.is_absolute() or path.as_posix()!=name or not 1<=len(path.parts)<=6
            or any(part in {'.','..'} or len(part)>100 or part.endswith('.') or part.endswith('.transfer-partial')
                   or part.split('.')[0].casefold() in {'con','prn','aux','nul',*[f'com{i}' for i in range(1,10)],*[f'lpt{i}' for i in range(1,10)]} for part in path.parts)
            or (len(path.parts)==1 and name not in ROOT_FILES)
            or (len(path.parts)>1 and path.parts[0] not in ROOT_DIRS)
            or name.casefold() in seen):
            raise ValueError('Escaping, duplicate or unsupported capture asset.')
        seen.add(name.casefold())
        size=binding['bytes']
        if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
            raise ValueError('Invalid capture asset size.')
        if not isinstance(binding['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',binding['sha256']):
            raise ValueError('Invalid capture asset digest.')
        total+=size
    if total>MAX_TOTAL_BYTES: raise ValueError('Capture exceeds transfer limit.')
    bound={entry['path']:entry for entry in files}
    if 'capture.json' not in bound or bound['capture.json']['sha256']!=hashlib.sha256(manifest).hexdigest() or bound['capture.json']['bytes']!=len(manifest):
        raise ValueError('Manifest does not bind inventory.')
    required={capture.get('video_file','video/capture.mov'),capture.get('frame_index_file','metadata/frame_index.jsonl')}
    for frame in capture['frames']:
        for key in ('rgb','depth','confidence','person_mask'):
            if isinstance(frame.get(key),str):required.add(frame[key])
    for key in ('arkit_mesh_file','arkit_mesh_report_file','planes_file','room_plan_file','room_plan_report_file','room_plan_semantics_file'):
        if isinstance(capture.get(key),str): required.add(capture[key])
    if not required.issubset(bound): raise ValueError('Full capture is missing referenced assets.')
    if not any(x.startswith('depth/') for x in bound) or not any(x.startswith('confidence/') for x in bound):
        raise ValueError('This RGB-D path requires depth and confidence assets.')
    return data, manifest


class FullCaptureStore(BridgeStore):
    def __init__(self,root,worker,runtime_ready=None):
        super().__init__(root,worker,runtime_ready,revision=REVISION)
        self.profile='rgbd'

    def create(self,body):
        descriptor,manifest=validate_descriptor(body)
        body=json.dumps(descriptor,sort_keys=True,separators=(',',':')).encode()
        identifier=hashlib.sha256(body).hexdigest()
        folder=self.folder(identifier)
        if folder.exists():
            state=self.state(identifier)
            if state['files']!=descriptor['files']:raise ValueError('Stored full inventory changed.')
            if state['status']=='failed' or (state['status']=='completed' and state.get('pipeline_revision')!=REVISION):
                return self._resubmit(identifier,state)
            return state
        if sum(1 for _ in islice(self.root.iterdir(),8))>=8:raise ValueError('Archive old full captures first.')
        (folder/'capture').mkdir(parents=True)
        (folder/'descriptor.json').write_bytes(body)
        (folder/'capture/capture.json').write_bytes(manifest)
        files=descriptor['files']
        index=next(i for i,f in enumerate(files) if f['path']=='capture.json')
        state={'id':identifier,'status':'uploading','images':[f['path'] for f in files],
               'files':files,'uploaded':{str(index):{'bytes':len(manifest),'sha256':hashlib.sha256(manifest).hexdigest()}},
               'chunk_bytes':CHUNK_BYTES,'message':'Waiting for full capture assets.', 'decision':'hold',
               'attempt':1,'pipeline_revision':REVISION,'profile':'rgbd','revision_attempt':1,
               'attempts_by_revision':{REVISION:1}}
        self.save(identifier,state)
        return state

    def start(self,identifier):
        state=self.state(identifier)
        if state['status']=='uploading' and state.get('pipeline_revision')!=REVISION:
            # Keep interrupted chunks and verified assets resumable across an
            # upgrade. Only advance the revision once the full inventory passes.
            self._resubmit(identifier,state)
        return super().start(identifier)

    def _verified_uploads(self,identifier,state):
        descriptor=_bounded_file(self.folder(identifier)/'descriptor.json',self.root,24*1024**2)
        if hashlib.sha256(descriptor).hexdigest()!=identifier:raise ValueError('Descriptor changed.')
        data,manifest=validate_descriptor(descriptor)
        if data['files']!=state['files']:raise ValueError('Asset inventory changed.')
        for index,binding in enumerate(data['files']):
            if str(index) not in state['uploaded']:raise ValueError('Missing capture asset.')
            path=self.folder(identifier)/'capture'/binding['path']
            if path.stat().st_size!=binding['bytes'] or _bounded_digest(path,self.root,MAX_ASSET_BYTES)!=binding['sha256']:
                raise ValueError('Capture asset changed.')

    def upload_chunk(self,identifier,index,part,body,digest):
        state=self.state(identifier)
        if state['status']!='uploading' or not 0<=index<len(state['files']):raise ValueError('Closed upload.')
        binding=state['files'][index]
        count=math.ceil(binding['bytes']/CHUNK_BYTES)
        expected=min(CHUNK_BYTES,binding['bytes']-part*CHUNK_BYTES)
        if not 0<=part<count or len(body)!=expected or hashlib.sha256(body).hexdigest()!=digest:
            raise ValueError('Chunk bounds or digest mismatch.')
        if str(index) in state['uploaded']:return
        chunks=self.folder(identifier)/'chunks'/str(index)
        chunks.mkdir(parents=True,exist_ok=True)
        piece=chunks/str(part)
        if piece.exists():
            if _bounded_file(piece,self.root,CHUNK_BYTES)!=body:raise ValueError('Immutable chunk changed.')
        else:
            with piece.open('xb') as stream:stream.write(body)
        if not all((chunks/str(i)).is_file() for i in range(count)):return
        target=self.folder(identifier)/'capture'/binding['path']
        target.parent.mkdir(parents=True,exist_ok=True)
        for parent in [target,*target.parents]:
            if parent==self.root:break
            if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():raise ValueError('Aliased upload path.')
        temporary=target.with_name(target.name+'.transfer-partial')
        if target.exists():
            # Recover a crash after the verified rename but before state save.
            if target.stat().st_size!=binding['bytes'] or _bounded_digest(target,self.root,MAX_ASSET_BYTES)!=binding['sha256']:
                raise ValueError('Existing capture asset differs.')
        else:
            if temporary.exists():
                if temporary.is_symlink() or not temporary.is_file() or temporary.resolve().parent!=target.parent.resolve():
                    raise ValueError('Unexpected transfer temporary path.')
                temporary.unlink()  # Reserved transfer scratch, never a capture input.
            digest=hashlib.sha256()
            with temporary.open('xb') as output:
                for i in range(count):
                    chunk=_bounded_file(chunks/str(i),self.root,CHUNK_BYTES)
                    digest.update(chunk);output.write(chunk)
            if digest.hexdigest()!=binding['sha256'] or temporary.stat().st_size!=binding['bytes']:
                raise ValueError('Complete asset digest mismatch.')
            temporary.rename(target)
        state['uploaded'][str(index)]={'bytes':binding['bytes'],'sha256':binding['sha256']}
        self.save(identifier,state)
        # Chunk copies are temporary transfer data; the verified original is retained.
        for i in range(count):(chunks/str(i)).unlink()
        chunks.rmdir()

    def _verified_result(self,identifier,receipt):
        if receipt.get('pipeline_revision')!=REVISION or receipt.get('profile')!='rgbd' or receipt.get('result_format')!='ply':
            raise ValueError('RGB-D result contract mismatch.')
        folder=self.folder(identifier)
        model=_bounded_file(folder/'result/inference/gaussians.ply',self.root,64*1024**2)
        digest=hashlib.sha256(model).hexdigest()
        if digest!=receipt['sha256'] or len(model)!=receipt['bytes']:raise ValueError('RGB-D model mismatch.')
        report_body=_bounded_file(folder/'result/training-consumption.json',self.root,2*1024**2)
        report=strict_json(report_body)
        report_digest=hashlib.sha256(report_body).hexdigest()
        if (report_digest!=receipt['training_consumption_sha256'] or report['ply_sha256']!=digest
            or report['depth_loss_steps']<=0 or report['depth_samples_consumed']<=0
            or report.get('depth_loss_differentiable') is not True or not report.get('video_training_images')
            or report['source_manifest_sha256']!=_bounded_digest(folder/'capture/capture.json',self.root,16*1024**2)):
            raise ValueError('Trainer did not bind sensor consumption.')
        if report.get('camera_alignment_sha256'):
            alignment = _bounded_file(folder/'result/camera-alignment.json',self.root,4*1024**2)
            if hashlib.sha256(alignment).hexdigest()!=report['camera_alignment_sha256']:
                raise ValueError('Consumed alignment binding changed.')
            if report.get('data_factor') not in (1,2) or report.get('viewer_profile')!='classic-depth-v1':
                raise ValueError('Unsupported calibrated training profile.')
        return model,'ply',report_digest


    def _run(self,identifier):
        try:
            receipt=self.worker(self.folder(identifier))
            model,format_name,report_digest=self._verified_result(identifier,receipt)
            state=self.state(identifier)
            state.update(status='completed',message='RGB-D reconstruction ready for review; quality not verified.',
                         result_sha256=hashlib.sha256(model).hexdigest(),result_bytes=len(model),
                         result_format='ply',result_path='inference/gaussians.ply',
                         viewer_profile=receipt.get('viewer_profile','classic-depth-v1'),
                         training_resolution_factor=receipt.get('training_resolution_factor',2),
                         training_consumption_sha256=report_digest,viewer_camera=receipt['viewer_camera'])
            self.save(identifier,state)
        except Exception as exc:
            state=self.state(identifier)
            state.update(status='failed',message='Full-scan reconstruction failed. Check the retained PC log.')
            self.save(identifier,state)
            with (self.folder(identifier)/'worker.log').open('a') as log:log.write(f'\n{type(exc).__name__}: {exc}\n')
        finally:self.active.release()

    def model_bytes(self,identifier,state):
        receipt={'pipeline_revision':REVISION,'profile':'rgbd','result_format':'ply',
                 'sha256':state['result_sha256'],'bytes':state['result_bytes'],
                 'training_consumption_sha256':state['training_consumption_sha256']}
        return self._verified_result(identifier,receipt)[0]


def rgbd_worker(repo):
    def run(folder):
        command=['wsl','-d','CaptureSplat','-u','root','--cd',str(repo),'--',
                 '/opt/capture-splat-rgbd/bin/python','scripts/run_rgbd_worker.py',
                 '--capture',(folder/'capture').relative_to(repo).as_posix(),
                 '--out',(folder/'result').relative_to(repo).as_posix()]
        with (folder/'worker.log').open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=7200)
        return strict_json((folder/'result/result.json').read_bytes())
    return run

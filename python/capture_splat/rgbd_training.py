"""Sensor samples and preparation for a pinned external gsplat trainer."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image
from .capture_schema import load_capture, iter_frames
from .colmap_export import export_colmap_text
from .json_utils import load_json_strict, write_json_strict
from .prepare_capture import prepare_capture
from .rgbd_seed import _frame_points, _mesh_points, _voxel_downsample, _append_colmap_points

TRAINER_COMMIT='937e29912570c372bed6747a5c9bf85fed877bae'
TRAINER_SHA256='79319e1cd7404e4d1ba0c425634235c39e6054f0643b904a01feea6179462c05'


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024**2):digest.update(chunk)
    return digest.hexdigest()


def sensor_samples(root,frame,K,image_size,maximum=4096):
    """Return image pixels and optical-Z measurements; no depth for video-only RGB."""
    if not isinstance(frame.get('depth'),str):
        return np.empty((0,2),np.float32),np.empty(0,np.float32),{}
    consumed={}
    for key in ('depth','confidence'):
        relative=frame.get(key)
        if not isinstance(relative,str):raise ValueError('RGB-D frame requires confidence.')
        path=Path(root)/relative
        if path.stat().st_size>4*1024**2:raise ValueError('Oversized sensor grid.')
        consumed[relative]=sha256(path)
    depth=np.load(Path(root)/frame['depth'],allow_pickle=False)
    confidence=np.load(Path(root)/frame['confidence'],allow_pickle=False)
    if depth.ndim!=2 or depth.shape!=confidence.shape or depth.size>1024**2:
        raise ValueError('Depth/confidence shapes differ.')
    scale=float(frame.get('depth_scale',1))
    if not np.isfinite(scale) or scale<=0:raise ValueError('Invalid sensor depth scale.')
    depths=depth.astype(np.float64)*scale
    valid=np.isfinite(depths)&(depths>0.05)&(depths<20)&(confidence>=1)&(confidence<=2)
    if isinstance(frame.get('valid_mask'),str):
        mask=Path(root)/frame['valid_mask'];consumed[frame['valid_mask']]=sha256(mask)
        with Image.open(mask) as image:
            valid &= np.asarray(image.convert('L').resize((depth.shape[1],depth.shape[0]),Image.Resampling.NEAREST))>=128
    ys,xs=np.nonzero(valid)
    if len(xs)>maximum:
        indices=np.linspace(0,len(xs)-1,maximum,dtype=int);ys,xs=ys[indices],xs[indices]
    intr=frame['intrinsics'];h,w=depth.shape
    fx=intr['fl_x']*w/intr['w'];fy=intr['fl_y']*h/intr['h']
    cx=intr['cx']*w/intr['w'];cy=intr['cy']*h/intr['h']
    if min(fx,fy)<=0:raise ValueError('Invalid calibrated focal length.')
    points=np.stack(((xs-cx)/fx*K[0,0]+K[0,2],(ys-cy)/fy*K[1,1]+K[1,2]),axis=-1)
    width,height=image_size
    inside=(points[:,0]>=0)&(points[:,0]<width)&(points[:,1]>=0)&(points[:,1]<height)
    return points[inside].astype(np.float32),depths[ys,xs][inside].astype(np.float32),consumed


def prepare_rgbd(capture,out,target_frames=300,max_edge=3840):
    capture=Path(capture).resolve();out=Path(out).resolve()
    if out.exists():raise ValueError('Use a fresh RGB-D output directory.')
    if out.is_relative_to(capture):raise ValueError('Output must be outside original capture.')
    source_hash=sha256(capture/'capture.json')
    raw_source=load_capture(capture)
    source_names={'capture.json',raw_source.get('video_file','video/capture.mov'),raw_source.get('frame_index_file','metadata/frame_index.jsonl')}
    for raw in raw_source['frames']:
        source_names.update(raw[key] for key in ('rgb','depth','confidence','person_mask') if isinstance(raw.get(key),str))
    for key in ('arkit_mesh_file','arkit_mesh_report_file'):
        if isinstance(raw_source.get(key),str):source_names.add(raw_source[key])
    source_bindings={}
    for name in sorted(source_names):
        path=capture/name
        if path.is_symlink() or not path.resolve().is_relative_to(capture):raise ValueError('Escaping source asset.')
        source_bindings[name]=sha256(path)
    out.mkdir(parents=True)
    scratch=out/'scratch';scratch.mkdir()
    previous=tempfile.tempdir;tempfile.tempdir=str(scratch)
    try: summary=prepare_capture(capture,out/'prepared',target_frames=target_frames,max_edge=max_edge)
    finally:tempfile.tempdir=previous
    frames_root=out/'prepared/frames';data=load_capture(frames_root)
    parsed=list(iter_frames(data,accepted_only=True))
    package=out/'dataset';export_colmap_text(frames_root,package)
    chunks=[];colors=[];consumed={}
    for frame in parsed:
        raw=data['frames'][frame.source_index-1]
        points,rgb=_frame_points(frames_root,frame,raw,900,1,float(data.get('depth_scale',1)))
        if len(points):
            chunks.append(points);colors.append(rgb)
            for key in ('depth','confidence','rgb'):
                relative=raw.get(key)
                if isinstance(relative,str):consumed[relative]=sha256(frames_root/relative)
    depth_points=sum(map(len,chunks))
    if not depth_points:raise ValueError('No valid LiDAR seed points; no photogrammetry-only fallback.')
    mesh_points,mesh_colors,mesh_report=_mesh_points(frames_root,data,parsed,.05,20000)
    if len(mesh_points):chunks.append(mesh_points);colors.append(mesh_colors)
    points,rgb=_voxel_downsample(np.concatenate(chunks),np.concatenate(colors),.03,80000)
    _append_colmap_points(package/'sparse/0/points3D.txt',points,rgb)
    mapping={f'{frame.index:06d}{Path(frame.image_path).suffix.lower()}':data['frames'][frame.source_index-1] for frame in parsed}
    write_json_strict(package/'sensor-index.json',{'schema':'capture_splat.gsplat_sensor_index.v1','frames':mapping,
                      'prepared_manifest_sha256':sha256(frames_root/'capture.json')})
    if sha256(capture/'capture.json')!=source_hash:raise ValueError('Source manifest changed.')
    report={'schema':'capture_splat.rgbd_preparation.v1','decision':'hold','source_manifest_sha256':source_hash,
            'prepared_manifest_sha256':sha256(frames_root/'capture.json'),'image_count':len(parsed),
            'continuous_video_images':summary['continuous_video_supplements'],
            'rgbd_seed_points_before_merge':depth_points,'mesh_seed_points':len(mesh_points),'initial_points':len(points),
            'mesh':mesh_report,'sensor_assets':consumed,'pose_source':'ARKit priors; no SfM refinement',
            'camera_conversion':'ARKit/OpenGL c2w @ diag(1,-1,-1,1) exactly once',
            'normalization':'disabled; source ARKit world frame retained',
            'source_asset_sha256':source_bindings,'dataset_points_sha256':sha256(package/'sparse/0/points3D.txt'),
            'originals_preserved':True,'authority':{'metric':False,'collision':False,'navigation':False,'visual_quality':False}}
    for name,digest in source_bindings.items():
        if sha256(capture/name)!=digest:raise ValueError('Source asset changed during preparation.')
    write_json_strict(out/'preparation.json',report)
    return report


def metric_depth_loss(predicted,measured):
    """Bounded metric Huber loss over rendered sensor rays, excluding empty rays."""
    import torch
    import torch.nn.functional as F
    valid=torch.isfinite(predicted)&torch.isfinite(measured)&(predicted>=.05)&(predicted<=20)&(measured>=.05)&(measured<=20)
    if not bool(valid.any()):return predicted.sum()*0,measured[valid]
    return F.smooth_l1_loss(predicted[valid],measured[valid],beta=.1),measured[valid]

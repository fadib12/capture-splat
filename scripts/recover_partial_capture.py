"""Recover posed video frames from a partial capture, preserving immutable inputs.

No missing RGB-D samples are synthesized. Output remains a held video-only
reconstruction proposal, with original finalization evidence retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
import numpy as np
from PIL import Image
from capture_splat.capture_schema import load_capture
from capture_splat.colmap_export import export_colmap_text
from capture_splat.json_utils import ensure_finite,load_json_strict,write_json_strict,reject_constant


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def inventory(root):
    stack=[(root,0)];records={};identities=set();entries=0;directories=0;total=0;name_bytes=0
    while stack:
        directory,level=stack.pop();directories+=1
        if level>8 or directories>128:raise ValueError('Source directory budget exceeded.')
        with os.scandir(directory) as listing:
            for item in listing:
                entries+=1;name_bytes+=len(item.name.encode('utf-8'))
                if entries>4096 or name_bytes>1024**2:raise ValueError('Source entry budget exceeded.')
                path=Path(item.path)
                if item.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Source aliases are unsupported.')
                info=path.lstat()
                if stat.S_ISDIR(info.st_mode):stack.append((path,level+1));continue
                if not stat.S_ISREG(info.st_mode):raise ValueError('Source must contain regular files.')
                total+=info.st_size
                if info.st_size>2*1024**3 or total>4*1024**3:raise ValueError('Source byte budget exceeded.')
                identity=(info.st_dev,info.st_ino)
                if info.st_ino and identity in identities:raise ValueError('Source inode alias detected.')
                identities.add(identity)
                name=path.relative_to(root).as_posix()
                if any(name.casefold()==key.casefold() for key in records):raise ValueError('Case alias detected.')
                records[name]={'bytes':info.st_size,'sha256':sha(path)}
    return records


def read_index(path):
    if not 0<path.stat().st_size<=128*1024**2:raise ValueError('Frame index exceeds limits.')
    records=[];previous=-1.
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if len(line)>65536 or len(records)>=100000:raise ValueError('Frame index record budget exceeded.')
            row=json.loads(line,parse_constant=reject_constant);ensure_finite(row)
            if type(row.get('video_frame_idx')) is not int or row['video_frame_idx']!=len(records):raise ValueError('Incomplete or duplicate video trajectory.')
            pose=np.asarray(row.get('camera_to_world'),dtype=float)
            if pose.shape!=(4,4) or not np.isfinite(pose).all() or not np.allclose(pose[3],[0,0,0,1],atol=1e-5):raise ValueError('Invalid source pose.')
            rotation=pose[:3,:3]
            if not np.allclose(rotation.T@rotation,np.eye(3),atol=.001) or abs(np.linalg.det(rotation)-1)>.001:raise ValueError('Invalid source camera axes.')
            intr=row.get('intrinsics',{})
            if not all(isinstance(intr.get(k),(float,int)) and not isinstance(intr[k],bool) and np.isfinite(intr[k]) for k in ('fl_x','fl_y','cx','cy','w','h')):raise ValueError('Missing calibrated intrinsics.')
            if min(intr['fl_x'],intr['fl_y'],intr['w'],intr['h'])<=0:raise ValueError('Invalid calibrated intrinsics.')
            timestamp=float(row['timestamp'])
            if timestamp<=previous:raise ValueError('Non-monotonic video index.')
            previous=timestamp;records.append(row)
    if not records:raise ValueError('Empty video index.')
    return records


def portable_exports(capture,out,manifest):
    summary_path=capture/'capture_splat_frames_summary.json'
    summary=load_json_strict(summary_path)
    summary.update(video='video/capture.mov',output_dir='.')
    write_json_strict(summary_path,summary)
    frames=[]
    for frame in manifest['frames']:
        intr=frame['intrinsics']
        frames.append(dict(file_path=frame['rgb'],transform_matrix=frame['transform_matrix'],
                           timestamp=frame['timestamp'],source_video_frame=frame['source_video_frame'],**intr))
    write_json_strict(capture/'transforms.json',{'camera_model':'OPENCV','k1':0,'k2':0,'p1':0,'p2':0,
                      'frames':frames,'coordinate_convention':'OpenGL camera-to-world; ARKit world retained',
                      'recovery_depth_available':False})
    (capture/'RECOVERY_README.txt').write_text(
        'Recovered video-only capture. The original video, camera index, mesh and RoomPlan files are preserved.\n'
        'capture.json and transforms.json contain normally tracked video frames with their exact recorded poses.\n'
        'Calibration is per frame. Images retain the full source resolution up to a 3840-pixel edge.\n'
        'No per-frame LiDAR depth was recorded; no depth maps were invented.\n'
        'metadata/finalization_report.json is the unchanged original failure receipt.\n'
        'metadata/recovery_report.json describes the separate recovery and its limits.\n'
        'This is input recovery, not a trained Gaussian splat or proof of visual/metric quality.\n'
        'Reconstruction needs an RGB-only path; the measured-depth worker cannot train this package directly.\n'
        'A future trained model needs verified coordinate registration before using the preserved RoomPlan overlay.\n',encoding='utf-8')
    colmap_summary=out/'colmap/capture_splat_colmap_summary.json'
    if colmap_summary.exists():
        info=load_json_strict(colmap_summary)
        info.update(capture_dir='capture',output_dir='colmap',sparse_dir='colmap/sparse/0',paths_relative_to='recovery_root')
        write_json_strict(colmap_summary,info)


def recover(source,out,target=300):
    source=source.resolve();out=out.resolve()
    if not source.is_relative_to(ROOT) or not out.is_relative_to(ROOT):raise ValueError('Use source and output on the workspace data drive.')
    if out.is_relative_to(source) or source.is_relative_to(out):raise ValueError('Recovery output must be separate from the input.')
    if not 1<=target<=600:raise ValueError('Target must be 1..600.')
    bindings=inventory(source);index=read_index(source/'metadata/frame_index.jsonl')
    finalization=load_json_strict(source/'metadata/finalization_report.json')
    if finalization.get('video_writer_status')!='completed':raise ValueError('This recovery lane requires a finalized video container.')
    if finalization.get('video_frame_count')!=len(index):raise ValueError('Video receipt/index counts disagree.')
    out.mkdir(parents=True,exist_ok=False);scratch=out/'scratch';scratch.mkdir()
    capture=out/'capture'
    command=['wsl','-d','CaptureSplat','--cd',str(ROOT),'--','env','PYTHONPATH=python',
             'OPENBLAS_NUM_THREADS=1','OMP_NUM_THREADS=2','TMPDIR='+scratch.relative_to(ROOT).as_posix(),
             '/opt/capture-splat-rgbd/bin/python','-m','capture_splat.frames_extract',
             '--video',(source/'video/capture.mov').relative_to(ROOT).as_posix(),
             '--frame-index',(source/'metadata/frame_index.jsonl').relative_to(ROOT).as_posix(),
             '--out',capture.relative_to(ROOT).as_posix(),'--target-frames',str(target),'--max-edge','3840','--pick','sharpest']
    with (out/'extraction.log').open('x',encoding='utf-8') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=3600)
    summary=load_json_strict(capture/'capture_splat_frames_summary.json')
    if summary['total_video_frames']!=len(index):raise ValueError('Container frame count differs from exact pose trajectory.')
    manifest=load_capture(capture);selected=[];discarded=[]
    for frame in manifest['frames']:
        source_index=frame['source_video_frame'];original=index[source_index]
        if original['tracking_state']!='normal':discarded.append(source_index);continue
        if frame['transform_matrix']!=original['camera_to_world']:raise ValueError('Recovered pose differs from indexed source.')
        with Image.open(capture/frame['rgb']) as image:
            image.verify()
        frame.update(source_kind='recovered_continuous_video',depth_available=False,
                     acceptance_source='derived sharpness selection with normal recorded tracking; not an accepted native RGB-D keyframe')
        selected.append(frame)
    if len(selected)<8:raise ValueError('Insufficient normally tracked posed frames.')
    for name,binding in bindings.items():
        target_path=capture/name
        if target_path.exists():raise ValueError('Original asset conflicts with generated output.')
        target_path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,target_path)
        if target_path.stat().st_size!=binding['bytes'] or sha(target_path)!=binding['sha256']:raise ValueError('Copied source asset differs.')
    manifest.update(source='capture_splat.partial_video_recovery',capture_intent='full_room_semantic',
                    frames=selected,video_file='video/capture.mov',frame_index_file='metadata/frame_index.jsonl',
                    coordinate_frame='arkit_world',units='meters',recovery_report_file='metadata/recovery_report.json',
                    depth_available=False,authority={'pose_prior':'recorded_ARKit_frame_index','visual_quality':False,
                                                    'metric':False,'collision':False,'navigation':False})
    for key,name in [('room_plan_file','room_plan/room.usdz'),('room_plan_report_file','room_plan/room_plan_report.json'),
                     ('room_plan_semantics_file','room_plan/room_semantics.json'),('arkit_mesh_file','geometry/arkit_mesh.ply'),
                     ('arkit_mesh_report_file','geometry/arkit_mesh_report.json')]:
        if (capture/name).is_file():manifest[key]=name
    write_json_strict(capture/'capture.json',manifest)
    load_capture(capture)
    export_colmap_text(capture,out/'colmap')
    portable_exports(capture,out,manifest)
    if inventory(source)!=bindings:raise ValueError('Original capture changed during recovery.')
    recovered_bindings={f['rgb']:sha(capture/f['rgb']) for f in selected}
    report={'schema':'capture_splat.partial_recovery.v1','decision':'hold','source_name':source.name,
            'source_assets':bindings,'original_finalization':finalization,'originals_unchanged':True,
            'complete_video_frames':len(index),'recovered_normally_tracked_images':len(selected),
            'excluded_limited_tracking_frames':discarded,'pose_mapping':'exact video frame ordinal; OpenGL camera-to-world retained',
            'image_max_edge':3840,'per_frame_lidar_depth_recovered':0,'depth_synthesized':False,
            'preserved_roomplan':(capture/'room_plan/room.usdz').is_file(),'preserved_arkit_mesh':(capture/'geometry/arkit_mesh.ply').is_file(),
            'recovered_image_sha256':recovered_bindings,'recovered_manifest_sha256':sha(capture/'capture.json'),
            'native_capture_finalization_rewritten':False,'rgbd_worker_compatible':False,
            'next_stage':'RGB-only reconstruction with recorded camera priors and preserved mesh/layout evidence; no measured per-frame depth loss',
            'authority':{'visual_quality':False,'metric':False,'collision':False,'navigation':False}}
    write_json_strict(capture/'metadata/recovery_report.json',report)
    with zipfile.ZipFile(out/(source.name+'-recovered.zip'),'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        derived=inventory(capture)
        for name in sorted(derived):
            archive.write(capture/name,source.name+'-recovered/'+name,
                          compress_type=zipfile.ZIP_STORED if Path(name).suffix in ('.jpg','.mov','.usdz') else zipfile.ZIP_DEFLATED)
    write_json_strict(out/'recovery.json',report)
    print(json.dumps({k:report[k] for k in ('decision','originals_unchanged','recovered_normally_tracked_images','complete_video_frames','per_frame_lidar_depth_recovered')}))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--target-frames',type=int,default=300);a=p.parse_args();recover(a.source,a.out,a.target_frames)

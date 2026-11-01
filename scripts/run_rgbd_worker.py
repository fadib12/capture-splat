"""Prepare the complete capture and execute pinned gsplat with measured LiDAR loss."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from capture_splat.rgbd_training import prepare_rgbd,sha256
from capture_splat.json_utils import load_json_strict,write_json_strict
from capture_splat.ply_stats import inspect_ply
from capture_splat.full_capture_bridge import REVISION


def run(capture,out,steps=7000,reuse_prepared=False,data_factor=1,align_cameras=True):
    capture=Path(capture).resolve();out=Path(out).resolve()
    if reuse_prepared:
        preparation=load_json_strict(out/'preparation.json')
        if preparation['source_manifest_sha256']!=sha256(capture/'capture.json'):
            raise ValueError('Prepared evidence does not bind this capture.')
        if (out/'training').exists():raise ValueError('Archive the earlier training stage before a new run.')
    else:preparation=prepare_rgbd(capture,out)
    if preparation.get('dataset_points_sha256') and sha256(out/'dataset/sparse/0/points3D.txt')!=preparation['dataset_points_sha256']:
        raise ValueError('Initialization evidence changed.')
    if align_cameras and not (out/'camera-alignment.json').exists():
        from capture_splat.rgbd_alignment import refine
        refine(out)
    command=[sys.executable,str(ROOT/'scripts/gsplat_rgbd_entry.py'),'--out',str(out),'--steps',str(steps),'--data-factor',str(data_factor)]
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='2',MAX_JOBS='1',CUDA_HOME='/usr/local/cuda-12.8')
    env['PATH']='/usr/local/cuda-12.8/bin:'+env.get('PATH','')
    with (out/'training.log').open('x') as log:
        subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=5400)
    return finalize(capture,out,steps)


def finalize(capture,out,steps):
    """Verify an existing completed training stage before publishing its receipt."""
    capture=Path(capture).resolve();out=Path(out).resolve()
    preparation=load_json_strict(out/'preparation.json')
    model=out/f'training/ply/point_cloud_{steps-1}.ply'
    stats=inspect_ply(model)
    if not stats['finite'] or not 0<model.stat().st_size<=64*1024**2:
        raise ValueError('Trained model is non-finite or exceeds mobile limits.')
    destination=out/'inference';destination.mkdir(exist_ok=True)
    if (destination/'gaussians.ply').exists():
        if sha256(model)!=sha256(destination/'gaussians.ply'):raise ValueError('Existing inference model differs.')
    else:shutil.copyfile(model,destination/'gaussians.ply')
    ledger=load_json_strict(out/'training-ledger.json')
    if ledger['depth_loss_steps']<=0 or ledger['depth_samples_consumed']<=0 or not ledger['depth_loss_differentiable']:
        raise ValueError('Measured depth was not consumed in differentiable training.')
    for name,binding in ledger['sensor_assets'].items():
        if sha256(out/'prepared/frames'/name)!=binding:raise ValueError('Consumed sensor asset changed.')
    import numpy as np
    from PIL import Image
    factor=ledger.get('data_factor',2)
    if factor not in (1,2):raise ValueError('Unsupported training resolution factor.')
    for name,binding in ledger['training_images'].items():
        cached=out/'dataset'/ledger.get('training_cache','images_sensor_scaled')/(Path(name).stem+'.png')
        if sha256(cached)!=binding:raise ValueError('Consumed image changed.')
        with Image.open(out/'dataset/images'/name) as image, Image.open(cached) as trained:
            size=(image.width//factor,image.height//factor)
            expected=image.convert('RGB').resize(size,Image.Resampling.BICUBIC)
            if trained.size!=size or not np.array_equal(np.asarray(trained.convert('RGB')),np.asarray(expected)):
                raise ValueError('Consumed training pixels differ from the bound source image.')
    for name,binding in preparation.get('source_asset_sha256',{}).items():
        if sha256(capture/name)!=binding:raise ValueError('Capture evidence changed during reconstruction.')
    from capture_splat.rgbd_alignment import verify_alignment
    alignment=verify_alignment(out,ledger['camera_alignment_sha256']) if ledger.get('camera_alignment_sha256') else None
    digest=sha256(destination/'gaussians.ply')
    report=dict(ledger,source_manifest_sha256=sha256(capture/'capture.json'),
                preparation_sha256=sha256(out/'preparation.json'),ply_sha256=digest,
                initialized_from_captured_mesh_points=0 if alignment else preparation['mesh_seed_points'],
                initialization_points=alignment['initial_points'] if alignment else preparation['initial_points'],
                initialization_seed_sha256=alignment['seed_sha256'] if alignment else preparation['dataset_points_sha256'],
                initialized_from_rgbd_points=preparation['rgbd_seed_points_before_merge'],
                total_prepared_images=preparation['image_count'],
                video_supplements_prepared=preparation['continuous_video_images'],
                decision='hold',authority={'visual_quality':False,'metric':False,'collision':False,'navigation':False},
                raw_imu_directly_supervised=False,raw_gps_directly_supervised=False,
                normal_supervision_applied=False,source_pixel_verification='all consumed training images matched declared source resize')
    if report['source_manifest_sha256']!=preparation['source_manifest_sha256']:
        raise ValueError('Source capture changed during training.')
    write_json_strict(out/'training-consumption.json',report)
    if ledger.get('camera_alignment_sha256') and sha256(out/'camera-alignment.json')!=ledger['camera_alignment_sha256']:
        raise ValueError('Consumed camera alignment changed during training.')
    index=load_json_strict(out/'dataset/sensor-index.json')['frames']
    first=sorted(index)[0]
    pose=alignment['corrected_c2w'].get(first,index[first]['transform_matrix']) if alignment else index[first]['transform_matrix']
    receipt={'pipeline_revision':REVISION,'engine':'gsplat','profile':'rgbd',
             'result_format':'ply','result_path':'inference/gaussians.ply','sha256':digest,
             'bytes':model.stat().st_size,'training_consumption_sha256':sha256(out/'training-consumption.json'),
             'viewer_camera':{'convention':'opengl','position':[pose[i][3] for i in range(3)],
                              'rotation':[row[:3] for row in pose[:3]],'gravity_up':[0,1,0]},
             'viewer_profile':'classic-depth-v1','training_resolution_factor':ledger.get('data_factor',2),
             'model_coordinate_frame':'arkit_world','model_units':'meters',
             'source_capture_sha256':report['source_manifest_sha256'],
             'decision':'hold','quality_verified':False,'sensor_supervision_applied':True}
    write_json_strict(out/'result.json',receipt)
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--steps',type=int,default=7000);p.add_argument('--reuse-prepared',action='store_true')
    p.add_argument('--data-factor',type=int,choices=(1,2),default=1)
    p.add_argument('--no-camera-refinement',action='store_true',help='Frozen ARKit-pose control lane.')
    a=p.parse_args();print(json.dumps(run(a.capture,a.out,a.steps,a.reuse_prepared,a.data_factor,not a.no_camera_refinement),indent=2))

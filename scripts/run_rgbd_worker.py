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


def run(capture,out,steps=3000,reuse_prepared=False):
    capture=Path(capture).resolve();out=Path(out).resolve()
    if reuse_prepared:
        preparation=load_json_strict(out/'preparation.json')
        if preparation['source_manifest_sha256']!=sha256(capture/'capture.json'):
            raise ValueError('Prepared evidence does not bind this capture.')
        if (out/'training').exists():raise ValueError('Archive the earlier training stage before a new run.')
    else:preparation=prepare_rgbd(capture,out)
    if preparation.get('dataset_points_sha256') and sha256(out/'dataset/sparse/0/points3D.txt')!=preparation['dataset_points_sha256']:
        raise ValueError('Initialization evidence changed.')
    command=[sys.executable,str(ROOT/'scripts/gsplat_rgbd_entry.py'),'--out',str(out),'--steps',str(steps)]
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='2',MAX_JOBS='1',CUDA_HOME='/usr/local/cuda-12.8')
    env['PATH']='/usr/local/cuda-12.8/bin:'+env.get('PATH','')
    with (out/'training.log').open('x') as log:
        subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=5400)
    model=out/f'training/ply/point_cloud_{steps-1}.ply'
    stats=inspect_ply(model)
    if not stats['finite'] or not 0<model.stat().st_size<=64*1024**2:
        raise ValueError('Trained model is non-finite or exceeds mobile limits.')
    destination=out/'inference';destination.mkdir(exist_ok=False)
    shutil.copyfile(model,destination/'gaussians.ply')
    ledger=load_json_strict(out/'training-ledger.json')
    if ledger['depth_loss_steps']<=0 or ledger['depth_samples_consumed']<=0 or not ledger['depth_loss_differentiable']:
        raise ValueError('Measured depth was not consumed in differentiable training.')
    for name,binding in ledger['sensor_assets'].items():
        if sha256(out/'prepared/frames'/name)!=binding:raise ValueError('Consumed sensor asset changed.')
    for name,binding in ledger['training_images'].items():
        if sha256(out/'dataset/images_sensor_scaled'/(Path(name).stem+'.png'))!=binding:raise ValueError('Consumed image changed.')
    for name,binding in preparation.get('source_asset_sha256',{}).items():
        if sha256(capture/name)!=binding:raise ValueError('Capture evidence changed during reconstruction.')
    digest=sha256(destination/'gaussians.ply')
    report=dict(ledger,source_manifest_sha256=sha256(capture/'capture.json'),
                preparation_sha256=sha256(out/'preparation.json'),ply_sha256=digest,
                initialized_from_captured_mesh_points=preparation['mesh_seed_points'],
                initialized_from_rgbd_points=preparation['rgbd_seed_points_before_merge'],
                total_prepared_images=preparation['image_count'],
                video_supplements_prepared=preparation['continuous_video_images'],
                decision='hold',authority={'visual_quality':False,'metric':False,'collision':False,'navigation':False},
                raw_imu_directly_supervised=False,raw_gps_directly_supervised=False,
                normal_supervision_applied=False)
    if report['source_manifest_sha256']!=preparation['source_manifest_sha256']:
        raise ValueError('Source capture changed during training.')
    write_json_strict(out/'training-consumption.json',report)
    source=load_json_strict(capture/'capture.json')['frames'][0]
    pose=source.get('transform_matrix',source.get('camera_to_world'))
    receipt={'pipeline_revision':'gsplat-rgbd-v1','engine':'gsplat','profile':'rgbd',
             'result_format':'ply','result_path':'inference/gaussians.ply','sha256':digest,
             'bytes':model.stat().st_size,'training_consumption_sha256':sha256(out/'training-consumption.json'),
             'viewer_camera':{'convention':'opengl','position':[pose[i][3] for i in range(3)],
                              'rotation':[row[:3] for row in pose[:3]],'gravity_up':[0,1,0]},
             'decision':'hold','quality_verified':False,'sensor_supervision_applied':True}
    write_json_strict(out/'result.json',receipt)
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--steps',type=int,default=3000);p.add_argument('--reuse-prepared',action='store_true')
    a=p.parse_args();print(json.dumps(run(a.capture,a.out,a.steps,a.reuse_prepared),indent=2))

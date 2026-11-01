import numpy as np
from capture_splat.rgbd_alignment import project,accept_correction,calibration


def scene():
    rng=np.random.default_rng(7)
    xyz=rng.uniform([-1,-1,-4],[1,1,-2],(80,3))
    K=np.array([[800.,0,960],[0,800,720],[0,0,1.]])
    return xyz,K


def test_opengl_camera_faces_negative_z_and_flip_is_once():
    pixels,z=project(np.array([[0.,0,-2],[1.,1,-2]]),np.eye(4),np.array([[100.,0,50],[0,100,50],[0,0,1.]]))
    np.testing.assert_allclose(pixels,[[50,50],[100,0]])
    np.testing.assert_allclose(z,[2,2])


def test_metric_gate_accepts_known_small_pose_correction():
    xyz,K=scene();true=np.eye(4);prior=true.copy();prior[0,3]=.04
    pixels,_=project(xyz,true,K)
    accepted,record=accept_correction(prior,true,xyz,pixels,K)
    assert accepted and record['refined_median_pixels']<1e-8
    assert record['translation_change_m']==.04


def test_gate_rejects_bad_unbounded_and_reflected_poses():
    xyz,K=scene();prior=np.eye(4);pixels,_=project(xyz,prior,K)
    for delta in (.1,1.):
        candidate=prior.copy();candidate[0,3]=delta
        assert not accept_correction(prior,candidate,xyz,pixels,K)[0]
    reflected=prior.copy();reflected[0,0]=-1
    assert not accept_correction(prior,reflected,xyz,pixels,K)[0]
    assert not accept_correction(prior,prior,xyz[:5],pixels[:5],K)[0]


def test_full_resolution_intrinsics_keep_principal_point():
    frame={'intrinsics':dict(w=256,h=192,fl_x=200,fl_y=201,cx=127,cy=95)}
    full=calibration(frame,(1920,1440));half=calibration(frame,(960,720))
    np.testing.assert_allclose(full[:2],half[:2]*2)

import json
from pathlib import Path
import pytest
from capture_splat.rgbd_alignment import verify_alignment
from capture_splat.rgbd_training import sha256
from capture_splat.json_utils import write_json_strict,load_json_strict


@pytest.fixture
def alignment_package(tmp_path):
    out=tmp_path/'run'
    for directory in ('dataset/images','dataset/sparse/0','prepared/frames/depth'):(out/directory).mkdir(parents=True)
    (out/'dataset/images/000001.jpg').write_bytes(b'bound source image')
    (out/'prepared/frames/depth/1.npy').write_bytes(b'bound depth')
    seed=out/'dataset/sparse/0/aligned-points3D.txt';seed.write_text('# seed\n1 0 0 -2 128 128 128 0\n')
    frame={'transform_matrix':np.eye(4).tolist()}
    write_json_strict(out/'dataset/sensor-index.json',{'frames':{'000001.jpg':frame}})
    pose=np.eye(4);pose[0,3]=.02
    report={'schema':'capture_splat.rgbd_alignment.v1','index_sha256':sha256(out/'dataset/sensor-index.json'),
            'image_sha256':{'000001.jpg':sha256(out/'dataset/images/000001.jpg')},
            'sensor_assets':{'depth/1.npy':sha256(out/'prepared/frames/depth/1.npy')},
            'seed_path':'sparse/0/aligned-points3D.txt','seed_sha256':sha256(seed),
            'corrected_c2w':{'000001.jpg':pose.tolist()},'accepted_cameras':1,'initial_points':1}
    write_json_strict(out/'camera-alignment.json',report)
    return out


@pytest.mark.parametrize('relative',['dataset/sensor-index.json','dataset/images/000001.jpg',
                                     'prepared/frames/depth/1.npy','dataset/sparse/0/aligned-points3D.txt'])
def test_alignment_revalidation_rejects_any_changed_input(alignment_package,relative):
    out=alignment_package;verify_alignment(out,sha256(out/'camera-alignment.json'))
    with (out/relative).open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='changed'):verify_alignment(out)


def test_alignment_rejects_escaped_seed_reflections_and_report_changes(alignment_package):
    out=alignment_package;path=out/'camera-alignment.json';original=load_json_strict(path);digest=sha256(path)
    report=dict(original,seed_path='../outside')
    write_json_strict(path,report)
    with pytest.raises(ValueError,match='alignment changed'):verify_alignment(out,digest)
    with pytest.raises(ValueError,match='seed path'):verify_alignment(out)
    original['corrected_c2w']['000001.jpg'][0][0]=-1
    write_json_strict(path,original)
    with pytest.raises(ValueError,match='rotation'):verify_alignment(out)


def test_final_receipt_uses_consumed_corrected_seed_and_camera(alignment_package):
    from scripts.run_rgbd_worker import finalize
    out=alignment_package;capture=out.parent/'capture';capture.mkdir()
    write_json_strict(capture/'capture.json',{'synthetic_test_fixture':True})
    preparation={'source_manifest_sha256':sha256(capture/'capture.json'),'source_asset_sha256':{},
                 'mesh_seed_points':20,'rgbd_seed_points_before_merge':100,'initial_points':120,
                 'dataset_points_sha256':'0'*64,'image_count':1,'continuous_video_images':0}
    write_json_strict(out/'preparation.json',preparation)
    folder=out/'training/ply';folder.mkdir(parents=True)
    (folder/'point_cloud_9.ply').write_text('ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 -2\n')
    ledger={'depth_loss_steps':1,'depth_samples_consumed':1,'depth_loss_differentiable':True,
            'sensor_assets':{},'training_images':{},'video_training_images':[],
            'camera_alignment_sha256':sha256(out/'camera-alignment.json'),'data_factor':1}
    write_json_strict(out/'training-ledger.json',ledger)
    receipt=finalize(capture,out,10)
    report=load_json_strict(out/'training-consumption.json')
    assert receipt['training_resolution_factor']==1 and receipt['viewer_camera']['position']==[.02,0,0]
    assert report['initialized_from_captured_mesh_points']==0 and report['initialization_points']==1
    assert report['initialization_seed_sha256']==load_json_strict(out/'camera-alignment.json')['seed_sha256']
    assert receipt['decision']=='hold' and not receipt['quality_verified']
    assert finalize(capture,out,10)['sha256']==receipt['sha256']
    with (out/'dataset/sparse/0/aligned-points3D.txt').open('a') as stream:stream.write('# changed\n')
    with pytest.raises(ValueError,match='initialization changed'):finalize(capture,out,10)

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

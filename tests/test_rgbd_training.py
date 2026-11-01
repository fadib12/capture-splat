from pathlib import Path
import numpy as np
import pytest
from capture_splat.rgbd_training import sensor_samples


def test_sensor_samples_use_optical_depth_confidence_and_actual_resize(tmp_path):
    depth=np.array([[2.,np.nan],[4.,6.]],np.float32)
    confidence=np.array([[2,2],[0,1]],np.uint8)
    np.save(tmp_path/'d.npy',depth);np.save(tmp_path/'c.npy',confidence)
    frame={'depth':'d.npy','confidence':'c.npy','depth_scale':.5,
           'intrinsics':{'w':4,'h':4,'fl_x':4.,'fl_y':4.,'cx':2.,'cy':2.}}
    K=np.array([[8.,0,4.],[0,8.,4.],[0,0,1.]])
    pixels,values,bindings=sensor_samples(tmp_path,frame,K,(8,8))
    np.testing.assert_allclose(pixels,[[0,0],[4,4]])
    np.testing.assert_allclose(values,[1,3])
    assert set(bindings)=={'d.npy','c.npy'}


def test_video_has_no_fabricated_sensor_depth(tmp_path):
    points,depths,assets=sensor_samples(tmp_path,{},np.eye(3),(2,2))
    assert points.shape==(0,2) and depths.shape==(0,) and assets=={}


def test_rgbd_requires_matching_confidence_grid(tmp_path):
    np.save(tmp_path/'d.npy',np.ones((2,2)))
    with pytest.raises(ValueError,match='confidence'):
        sensor_samples(tmp_path,{'depth':'d.npy'},np.eye(3),(2,2))


def test_metric_sensor_loss_has_finite_gradients_and_ignores_empty_rays():
    torch=pytest.importorskip('torch')
    from capture_splat.rgbd_training import metric_depth_loss
    predicted=torch.tensor([1e-12,2.2],requires_grad=True)
    measured=torch.tensor([1.,2.])
    loss,used=metric_depth_loss(predicted,measured)
    assert used.numel()==1 and float(loss.detach())==pytest.approx(.15,abs=1e-6)
    loss.backward()
    assert torch.isfinite(predicted.grad).all()
    assert predicted.grad[0]==0 and predicted.grad[1]>0

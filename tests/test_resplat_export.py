import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from capture_splat.resplat_export import covariance_xyzw, verify_ply_covariances, viewer_gravity_up, world_rotations


def test_grouped_camera_rotations_recover_known_anisotropic_covariances():
    # Rotating diag(9, 1, 4) by 45 degrees around Z gives this off-diagonal
    # covariance. The camera-local quaternion alone cannot represent it.
    half = np.sqrt(.5)
    camera = np.array([[half, -half, 0], [half, half, 0], [0, 0, 1]])
    cameras = np.stack([camera, np.eye(3)])[None]
    scales = np.tile([3., 1., 2.], (1, 4, 1))
    local = np.tile([0., 0., 0., 1.], (1, 4, 1))
    rotated = np.array([[5., 4., 0.], [4., 5., 0.], [0., 0., 4.]])
    expected = np.stack([rotated, rotated, np.diag([9., 1., 4.]), np.diag([9., 1., 4.])])[None]
    result, report = world_rotations(scales, local, cameras, expected)
    np.testing.assert_allclose(covariance_xyzw(scales, result), expected, atol=1e-6)
    assert report['world_covariance_export_verified']
    assert report['gaussians_checked'] == 4
    assert report['maximum_relative_covariance_error'] < 1e-6
    assert report['old_maximum_relative_covariance_error'] > .8


def test_camera_rotation_precedes_nonidentity_local_rotation():
    # RX(90) @ RZ(90) maps the local scale axes onto Z, -X and -Y respectively.
    camera = np.array([[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]])
    local = np.array([[[0., 0., np.sqrt(.5), np.sqrt(.5)]]])
    scales = np.array([[[3., 1., 2.]]])
    expected = np.diag([1., 4., 9.])[None, None]
    result, _ = world_rotations(scales, local, camera[None, None], expected)
    np.testing.assert_allclose(covariance_xyzw(scales, result), expected, atol=1e-6)
    # Reversing the multiplication would give diag(4, 9, 1).
    assert not np.allclose(covariance_xyzw(scales, result), np.diag([4., 9., 1.]))


@pytest.mark.parametrize('fault', ['zero_quaternion', 'reflection', 'grouping', 'reference_mismatch'])
def test_invalid_export_cannot_claim_renderer_parity(fault):
    scales = np.array([[[3., 1., 2.]]])
    local = np.array([[[0., 0., 0., 1.]]])
    cameras = np.eye(3)[None, None]
    expected = np.diag([9., 1., 4.])[None, None]
    if fault == 'zero_quaternion':
        local[:] = 0
    elif fault == 'reflection':
        cameras[0, 0, 0, 0] = -1
    elif fault == 'grouping':
        cameras = np.tile(cameras, (1, 2, 1, 1))
    else:
        expected = np.eye(3)[None, None]
    with pytest.raises(ValueError):
        world_rotations(scales, local, cameras, expected)


def write_ply(path, scales, quaternion_wxyz):
    data = np.empty(1, dtype=[(f'scale_{i}', '<f4') for i in range(3)]
                           + [(f'rot_{i}', '<f4') for i in range(4)])
    for i in range(3):
        data[f'scale_{i}'] = np.log(scales[i])
    for i in range(4):
        data[f'rot_{i}'] = quaternion_wxyz[i]
    PlyData([PlyElement.describe(data, 'vertex')]).write(path)


def test_written_ply_verifies_world_covariance_and_view_alignment(tmp_path):
    path = tmp_path / 'checked.ply'
    # World covariance is diag(9,1,4), then the export view rotates it by 45deg.
    half = np.sqrt(.5)
    view = np.array([[half, -half, 0], [half, half, 0], [0, 0, 1]])
    write_ply(path, [3, 1, 2], [np.cos(np.pi/8), 0, 0, np.sin(np.pi/8)])
    report = verify_ply_covariances(path, np.diag([9., 1., 4.])[None], view)
    assert report['ply_covariance_verified']
    assert report['ply_maximum_relative_covariance_error'] < 1e-6


def test_written_camera_local_rotation_fails_world_covariance_check(tmp_path):
    path = tmp_path / 'legacy.ply'
    write_ply(path, [3, 1, 2], [1, 0, 0, 0])
    expected = np.array([[[5., 4., 0.], [4., 5., 0.], [0., 0., 4.]]])
    with pytest.raises(ValueError, match='Written PLY differs'):
        verify_ply_covariances(path, expected, np.eye(3))


def test_finite_scales_that_overflow_covariance_are_rejected():
    with pytest.raises(ValueError, match='Non-finite Gaussian covariance'):
        covariance_xyzw(np.array([[1e200, 1., 2.]]), np.array([[0., 0., 0., 1.]]))


def test_gravity_conversion_uses_pivot_sensor_rotation_and_camera_flip_once():
    np.testing.assert_allclose(viewer_gravity_up(np.eye(4), np.eye(3)), [0, -1, 0])
    portrait = np.array([[0., -1., 0., 0.], [1., 0., 0., 0.],
                         [0., 0., 1., 0.], [0., 0., 0., 1.]])
    np.testing.assert_allclose(viewer_gravity_up(portrait, np.eye(3)), [1, 0, 0])
    export_rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    np.testing.assert_allclose(viewer_gravity_up(portrait, export_rotation), [0, 1, 0])


def test_gravity_rejects_nonrigid_transform():
    invalid = np.eye(4)
    invalid[0, 0] = 2
    with pytest.raises(ValueError, match='proper rotations'):
        viewer_gravity_up(invalid, np.eye(3))

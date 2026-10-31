"""Convert camera-local ReSplat rotations to its renderer's world covariance."""
import numpy as np

from .colmap_export import rotation_matrix_to_quaternion_wxyz


def viewer_gravity_up(arkit_camera_to_world, export_view_rotation):
    """Express ARKit world-up in the exported, pivot-camera-aligned PLY frame."""
    pose = np.asarray(arkit_camera_to_world, dtype=np.float64)
    view = np.asarray(export_view_rotation, dtype=np.float64)
    if (pose.shape != (4, 4) or view.shape != (3, 3)
            or not np.isfinite(pose).all() or not np.isfinite(view).all()
            or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5, rtol=0)):
        raise ValueError('Invalid gravity coordinate transform.')
    for rotation in (pose[:3, :3], view):
        if (not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4, rtol=0)
                or np.linalg.det(rotation) < .999):
            raise ValueError('Gravity transform must contain proper rotations.')
    pivot_cv_rotation = pose[:3, :3] @ np.diag([1., -1., -1.])
    up = view @ pivot_cv_rotation.T @ np.array([0., 1., 0.])
    up /= np.linalg.norm(up)
    return up.tolist()


def covariance_xyzw(scales, quaternions):
    q = np.asarray(quaternions, dtype=np.float64)
    scales = np.asarray(scales, dtype=np.float64)
    if q.shape[-1:] != (4,) or scales.shape != (*q.shape[:-1], 3):
        raise ValueError('Unsupported Gaussian scale/quaternion layout.')
    norms = np.linalg.norm(q, axis=-1, keepdims=True)
    if not np.isfinite(q).all() or not np.isfinite(scales).all() or (scales <= 0).any() or (norms <= 1e-10).any():
        raise ValueError('Invalid Gaussian scale/quaternion.')
    q = q / norms
    v, w = q[..., :3], q[..., 3]
    cross = np.zeros((*q.shape[:-1], 3, 3))
    cross[..., 0, 1], cross[..., 0, 2] = -v[..., 2], v[..., 1]
    cross[..., 1, 0], cross[..., 1, 2] = v[..., 2], -v[..., 0]
    cross[..., 2, 0], cross[..., 2, 1] = -v[..., 1], v[..., 0]
    rotation = ((w*w - (v*v).sum(-1))[..., None, None] * np.eye(3)
                + 2*v[..., :, None]*v[..., None, :] + 2*w[..., None, None]*cross)
    with np.errstate(over='ignore', invalid='ignore'):
        covariance = (rotation * scales[..., None, :]**2) @ rotation.swapaxes(-1, -2)
    if not np.isfinite(covariance).all():
        raise ValueError('Non-finite Gaussian covariance.')
    return covariance


def world_rotations(scales, local_xyzw, camera_rotations, declared_covariances):
    scales, local, cameras, expected = [np.asarray(x, dtype=np.float64) for x in
                                       (scales, local_xyzw, camera_rotations, declared_covariances)]
    if local.ndim != 3 or local.shape[-1] != 4 or cameras.ndim != 4 or cameras.shape[-2:] != (3, 3):
        raise ValueError('Unsupported ReSplat Gaussian/camera layout.')
    batch, count, _ = local.shape
    views = cameras.shape[1]
    if not batch or not count or cameras.shape[0] != batch or not views or count % views or scales.shape != (batch, count, 3) or expected.shape != (batch, count, 3, 3):
        raise ValueError('Gaussian grouping does not match context cameras.')
    if not all(np.isfinite(x).all() for x in (scales, local, cameras, expected)) or (scales <= 0).any():
        raise ValueError('Invalid Gaussian inputs.')
    norms = np.linalg.norm(local, axis=-1, keepdims=True)
    if (norms <= 1e-10).any():
        raise ValueError('Zero Gaussian quaternion.')
    local = (local / norms).reshape(batch, views, count // views, 4)
    camera_q = np.empty((batch, views, 1, 4))
    for b in range(batch):
        for v in range(views):
            matrix = cameras[b, v]
            if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-4) or np.linalg.det(matrix) < .999:
                raise ValueError('Invalid camera rotation.')
            w, x, y, z = rotation_matrix_to_quaternion_wxyz(matrix)
            camera_q[b, v, 0] = [x, y, z, w]
    av, aw = camera_q[..., :3], camera_q[..., 3:]
    bv, bw = local[..., :3], local[..., 3:]
    world = np.concatenate((aw*bv + bw*av + np.cross(av, bv), aw*bw - (av*bv).sum(-1, keepdims=True)), axis=-1)
    world = world.reshape(batch, count, 4)
    world /= np.linalg.norm(world, axis=-1, keepdims=True)
    world = world.astype(np.float32)
    corrected = covariance_xyzw(scales, world)
    original = covariance_xyzw(scales, local.reshape(batch, count, 4))
    denominator = np.maximum(np.linalg.norm(expected, axis=(-2, -1)), 1e-20)
    error = np.linalg.norm(corrected-expected, axis=(-2, -1)) / denominator
    old_error = np.linalg.norm(original-expected, axis=(-2, -1)) / denominator
    if not np.isfinite(error).all() or error.max() > 1e-4:
        raise ValueError(f'Export covariance does not match internal rendering: {error.max():.6g}')
    return world, {'world_covariance_export_verified': True,
                   'gaussians_checked': count,
                   'maximum_relative_covariance_error': float(error.max()),
                   'old_maximum_relative_covariance_error': float(old_error.max())}


def verify_ply_covariances(path, world_covariances, view_rotation):
    """Read back the published PLY and compare its represented ellipsoids."""
    from plyfile import PlyData

    vertices = PlyData.read(path)['vertex'].data
    if not 0 < len(vertices) <= 2_000_000:
        raise ValueError('Unsupported PLY Gaussian count.')
    scales = np.exp(np.stack([vertices[f'scale_{i}'] for i in range(3)], axis=-1).astype(np.float64))
    # The upstream PLY exporter writes WXYZ; internal network quaternions are XYZW.
    rotations = np.stack([vertices[f'rot_{i}'] for i in (1, 2, 3, 0)], axis=-1)
    actual = covariance_xyzw(scales, rotations)
    rotation = np.asarray(view_rotation, dtype=np.float64)
    expected = rotation @ np.asarray(world_covariances, dtype=np.float64) @ rotation.T
    if expected.shape != actual.shape or not np.isfinite(expected).all():
        raise ValueError('PLY covariance reference mismatch.')
    relative = np.linalg.norm(actual-expected, axis=(-2, -1)) / np.maximum(np.linalg.norm(expected, axis=(-2, -1)), 1e-20)
    if not np.isfinite(relative).all() or relative.max() > 1e-4:
        raise ValueError(f'Written PLY differs from renderer covariance: {relative.max():.6g}')
    return {'ply_covariance_verified': True,
            'ply_maximum_relative_covariance_error': float(relative.max())}

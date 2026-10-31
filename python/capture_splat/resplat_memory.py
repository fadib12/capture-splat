"""Bounded inference correlation using the pinned external warping primitive.

This adapter preserves all view pairs, depth hypotheses, precision and reductions.
Only the temporary warped feature tensor is evaluated in smaller pieces.
"""
import ast
import hashlib
import json
from functools import partial
from pathlib import Path


UPSTREAM_FILE_SHA256 = 'd639361558ee51b74efa42110b75e82b3bcbc2b875c9c20bd4b1699fe6b14e6c'
UPSTREAM_BOUNDARY_AST_SHA256 = '87450085a9a7f7248a3e66afc15dc9899880b8f0c63ff650dc2391024e1a8b76'


def _ast_sha256(node):
    # ast.dump omits some empty fields on Python 3.13; serialize every field so
    # the gate has identical meaning in the Windows and Linux runtimes.
    def canonical(value):
        if isinstance(value, ast.AST):
            return {'_type': type(value).__name__,
                    **{key: canonical(item) for key, item in ast.iter_fields(value)}}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value
    return hashlib.sha256(json.dumps(canonical(node), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def chunked_cost_volume(ref_features, tgt_features, intrinsics, pose, depth_candidates,
                        sample_log_depth, grid_sample_disable_cudnn, *, warp,
                        depth_chunk=8, pair_batch=4, statistics=None):
    import torch

    if not 1 <= depth_chunk <= 128 or not 1 <= pair_batch <= 32:
        raise ValueError('Unsupported cost-volume chunk bounds.')
    batch, channels, height, width = ref_features.shape
    pairs = tgt_features.shape[1]
    count = batch * pairs
    depth_count = depth_candidates.shape[1]
    if (tgt_features.shape != (batch, pairs, channels, height, width)
            or intrinsics.shape != (batch, pairs, 3, 3) or pose.shape != (batch, pairs, 4, 4)
            or depth_candidates.shape != (count, depth_count, height, width)
            or not pairs or not depth_count or ref_features.dtype != torch.float32
            or tgt_features.dtype != torch.float32 or depth_candidates.dtype != torch.float32):
        raise ValueError('Unsupported pinned cost-volume tensor layout or precision.')
    targets = tgt_features.reshape(count, channels, height, width)
    # The external warper switches off cuDNN for large input tensors. Preserve
    # the unchunked decision even when one of our pair batches is smaller.
    disable_cudnn = grid_sample_disable_cudnn or targets.numel() > 1_000_000
    camera_intrinsics = intrinsics.reshape(count, 3, 3)
    relative_poses = pose.reshape(count, 4, 4)
    correlations = ref_features.new_empty(count, depth_count, height, width)
    for pair_start in range(0, count, pair_batch):
        pair_stop = min(pair_start + pair_batch, count)
        reference_indices = torch.arange(pair_start, pair_stop, device=ref_features.device) // pairs
        references = ref_features.index_select(0, reference_indices)
        for depth_start in range(0, depth_count, depth_chunk):
            depth_stop = min(depth_start + depth_chunk, depth_count)
            candidates = depth_candidates[pair_start:pair_stop, depth_start:depth_stop]
            depths = torch.exp(candidates) if sample_log_depth else 1.0 / candidates
            warped = warp(targets[pair_start:pair_stop], camera_intrinsics[pair_start:pair_stop],
                          relative_poses[pair_start:pair_stop], depths,
                          grid_sample_disable_cudnn=disable_cudnn)
            correlation = (references.unsqueeze(2) * warped).sum(1) / channels**.5
            correlations[pair_start:pair_stop, depth_start:depth_stop] = correlation
            del warped, correlation
    if statistics is not None:
        statistics['cost_volume_calls'] += 1
    return correlations.reshape(batch, pairs, depth_count, height, width).mean(1)


def install_chunked_cost_volume(module, *, depth_chunk=8, pair_batch=4):
    """Patch one checked external function in memory; never edit its source files."""
    path = Path(module.__file__)
    if path.stat().st_size > 256 * 1024:
        raise ValueError('Oversized upstream cost-volume source.')
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != UPSTREAM_FILE_SHA256:
        raise ValueError('Pinned cost-volume source bytes changed.')
    tree = ast.parse(source)
    encoder = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MultiViewUniMatch')
    forward = next(node for node in encoder.body if isinstance(node, ast.FunctionDef) and node.name == 'forward')
    matches = []
    for node in ast.walk(forward):
        if isinstance(node, ast.For):
            for index in range(len(node.body) - 2):
                boundary = ast.Module(body=node.body[index:index+3], type_ignores=[])
                digest = _ast_sha256(boundary)
                if digest == UPSTREAM_BOUNDARY_AST_SHA256:
                    matches.append((node, index))
    if len(matches) != 1:
        raise ValueError('Pinned cost-volume AST boundary changed.')
    loop, index = matches[0]
    replacement = ast.parse('cost_volume = _capture_splat_chunked_cost_volume('
                            'ref_features, tgt_features, intrinsics_input, pose_curr, depth_candidates_curr, '
                            'self.sample_log_depth, self.grid_sample_disable_cudnn)').body[0]
    loop.body[index:index+3] = [ast.copy_location(replacement, loop.body[index])]
    statistics = dict(adapter_revision='depth-correlation-chunks-v1',
                      upstream_source_sha256=UPSTREAM_FILE_SHA256,
                      upstream_boundary_ast_sha256=UPSTREAM_BOUNDARY_AST_SHA256,
                      depth_chunk=depth_chunk, pair_batch=pair_batch, cost_volume_calls=0,
                      precision_preserved=True, depth_candidates_preserved=True,
                      context_views_preserved=True, image_resolution_preserved=True)
    namespace = dict(module.__dict__)
    namespace['_capture_splat_chunked_cost_volume'] = partial(
        chunked_cost_volume, warp=module.warp_with_pose_depth_candidates,
        depth_chunk=depth_chunk, pair_batch=pair_batch, statistics=statistics)
    patched = ast.fix_missing_locations(ast.Module(body=[forward], type_ignores=[]))
    exec(compile(patched, str(path), 'exec'), namespace)
    module.MultiViewUniMatch.forward = namespace['forward']
    return statistics

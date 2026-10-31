"""Use pinned upstream inference with a checked world-space export adapter."""
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from capture_splat.resplat_export import world_rotations, verify_ply_covariances
from capture_splat.resplat_adapter import sha256


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve(strict=True)
    spec = importlib.util.spec_from_file_location('resplat_upstream', root / 'scripts/infer_colmap.py')
    upstream = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = upstream
    spec.loader.exec_module(upstream)
    memory_adapter = None
    if os.environ.get('CAPTURE_SPLAT_RESPLAT_MEMORY') == 'chunked':
        from src.model.encoder.unimatch import mv_unimatch
        from capture_splat.resplat_memory import install_chunked_cost_volume
        memory_adapter = install_chunked_cost_volume(mv_unimatch)
        print('Pinned cost-volume memory adapter enabled:', memory_adapter)
    original_save = upstream.save_outputs

    def checked_save(rendered, gaussians, batch, output_dir, *args, **kwargs):
        import torch
        rotations, report = world_rotations(
            gaussians.scales.detach().cpu().numpy(), gaussians.rotations.detach().cpu().numpy(),
            batch['context']['extrinsics'][..., :3, :3].detach().cpu().numpy(),
            gaussians.covariances.detach().cpu().numpy())
        exported = dataclasses.replace(gaussians, rotations=torch.from_numpy(rotations).to(gaussians.rotations))
        original_save(rendered, exported, batch, output_dir, *args, **kwargs)
        context_poses = batch['context']['extrinsics'][0].detach().cpu().numpy()
        pivot = len(context_poses) // 2
        import numpy as np
        view_rotation = np.linalg.inv(context_poses[pivot, :3, :3])
        model = Path(output_dir) / 'gaussians.ply'
        report.update(verify_ply_covariances(model, gaussians.covariances[0].detach().cpu().numpy(), view_rotation))
        report.update(ply_sha256=sha256(model),
                      memory_adapter=memory_adapter,
                      pivot_context_index=pivot,
                      comparison_frame='renderer_world_then_upstream_view_rotation',
                      context_image_names=list(kwargs.get('context_image_names') or []),
                      target_image_names=list(args[0]) if args else [],
                      context_camera_to_world=context_poses.tolist(),
                      context_intrinsics=batch['context']['intrinsics'][0].detach().cpu().numpy().tolist(),
                      target_camera_to_world=batch['target']['extrinsics'][0].detach().cpu().numpy().tolist(),
                      target_intrinsics=batch['target']['intrinsics'][0].detach().cpu().numpy().tolist(),
                      render_height=int(rendered.shape[-2]), render_width=int(rendered.shape[-1]),
                      export_view_rotation=view_rotation.tolist())
        (Path(output_dir) / 'covariance-export.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        print('World-space Gaussian export verified:',
              {key: value for key, value in report.items() if key.endswith('error') or key.endswith('verified')})

    upstream.save_outputs = checked_save
    sys.argv = [str(root / 'scripts/infer_colmap.py'), *sys.argv[2:]]
    upstream.main()

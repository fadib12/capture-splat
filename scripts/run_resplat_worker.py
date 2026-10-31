"""Run the pinned external pretrained model in its isolated Linux environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from capture_splat.json_utils import load_json_strict
from capture_splat.capture_schema import iter_frames, load_capture
from capture_splat.resplat_adapter import (SOURCE_COMMIT, inference_command, prepare,
                                           profile_config, sha256)
from capture_splat.resplat_export import viewer_gravity_up
from export_mobile_splat import export


def prefer_full_ply(capture: Path, out: Path, receipt: dict, preparation: dict) -> dict:
    """Bind full SH PLY as the preferred viewer result without re-running inference."""
    model = out / 'inference/gaussians.ply'
    report = receipt['covariance_export']
    if sha256(model) != report['ply_sha256'] or sha256(capture / 'capture.json') != preparation['source_manifest_sha256']:
        raise ValueError('Model or source pose evidence changed before publishing.')
    prepared_frames = {Path(frame['output_path']).name: frame for frame in preparation['frames']}
    context_frames = [prepared_frames[name] for name in report['context_image_names']]
    pivot_source = context_frames[report['pivot_context_index']]
    source_frame = next(frame for frame in iter_frames(load_capture(capture))
                        if frame.source_index == pivot_source['source_frame_index'])
    if source_frame.image_path != pivot_source['source_path']:
        raise ValueError('Pivot source image and pose binding mismatch.')
    compact_path = out / 'model.splat'
    compact_receipt = load_json_strict(out / 'model.splat.json')
    if (sha256(compact_path) != compact_receipt['sha256']
            or compact_path.stat().st_size != compact_receipt['bytes']
            or compact_receipt['source_sha256'] != report['ply_sha256']):
        raise ValueError('Compact fallback does not bind the same PLY.')
    receipt.update(result_format='ply', result_path='inference/gaussians.ply',
                   format='gaussian_ply', sha256=report['ply_sha256'], bytes=model.stat().st_size,
                   color='spherical_harmonics_full', higher_order_sh_preserved=True,
                   compact_splat={'path': 'model.splat', 'sha256': compact_receipt['sha256'],
                                  'bytes': compact_receipt['bytes'], 'format': compact_receipt['format']},
                   context_source_frames=context_frames, pivot_source_frame=pivot_source)
    receipt['viewer_camera']['gravity_up'] = viewer_gravity_up(source_frame.transform_matrix,
                                                              report['export_view_rotation'])
    return receipt


def run(capture: Path, out: Path, root: Path, profile: str = 'small') -> dict:
    capture, out, root = capture.resolve(), out.resolve(), root.resolve()
    config = profile_config(profile)
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != SOURCE_COMMIT or sha256(root / 'pretrained' / config['checkpoint']) != config['sha256']:
        raise ValueError('External ReSplat source/checkpoint pin mismatch.')
    preparation = prepare(capture, out)
    command = inference_command(root, out / 'scene', out / 'inference', sys.executable, profile=profile)
    # Keep the external implementation pinned; adapt only its export boundary.
    command = [command[0], str(Path(__file__).with_name('resplat_inference_entry.py')),
               str(root), *command[2:]]
    env = dict(os.environ, CUDA_HOME='/usr/local/cuda-12.8', MAX_JOBS='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='2', TORCH_CUDA_ARCH_LIST='8.6',
               WANDB_MODE='disabled', PYTHONUNBUFFERED='1')
    env['PATH'] = '/usr/local/cuda-12.8/bin:' + env.get('PATH', '')
    with (out / 'inference.log').open('x') as log:
        subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=1800)
    model = out / 'inference/gaussians.ply'
    if not model.is_file() or model.stat().st_size > 512 * 1024**2:
        raise ValueError('ReSplat did not produce a bounded Gaussian PLY.')
    covariance_path = out / 'inference/covariance-export.json'
    if not covariance_path.is_file() or covariance_path.stat().st_size > 1024**2:
        raise ValueError('ReSplat did not produce a bounded covariance export check.')
    covariance_report = load_json_strict(covariance_path)
    if (covariance_report.get('world_covariance_export_verified') is not True
            or covariance_report.get('ply_covariance_verified') is not True
            or covariance_report.get('ply_sha256') != sha256(model)):
        raise ValueError('ReSplat covariance check does not bind the exported PLY.')
    receipt = export(model, out / 'model.splat')
    pivot = covariance_report['pivot_context_index']
    intrinsics = covariance_report['context_intrinsics'][pivot]
    width, height = covariance_report['render_width'], covariance_report['render_height']
    prepared_frames = {Path(frame['output_path']).name: frame for frame in preparation['frames']}
    context_frames = [prepared_frames[name] for name in covariance_report['context_image_names']]
    receipt.update(decision='hold', engine='resplat', source_commit=actual,
                   pipeline_revision=f'resplat-worldcov-v2-{profile}', profile=profile,
                   preset=config['preset'], image_shape=config['image_shape'],
                   checkpoint_sha256=config['sha256'],
                   covariance_export=covariance_report,
                   covariance_export_report='inference/covariance-export.json',
                   covariance_export_report_sha256=sha256(covariance_path),
                   context_source_frames=context_frames,
                   pivot_source_frame=context_frames[pivot],
                   coordinate_frame='upstream_context_view_aligned_not_arkit_world',
                   viewer_camera={'position': [0, 0, 0], 'rotation': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                  'fx': intrinsics[0][0] * width, 'fy': intrinsics[1][1] * height,
                                  'cx': intrinsics[0][2] * width, 'cy': intrinsics[1][2] * height,
                                  'width': width, 'height': height})
    prefer_full_ply(capture, out, receipt, preparation)
    (out / 'result.json').write_text(json.dumps(receipt, indent=2, allow_nan=False))
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--root', type=Path, default=Path('external/resplat'))
    p.add_argument('--profile', choices=('small', 'detail'), default='small')
    args = p.parse_args()
    print(json.dumps(run(args.capture, args.out, args.root, args.profile), indent=2))

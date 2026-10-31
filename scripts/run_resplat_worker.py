"""Run the pinned external pretrained model in its isolated Linux environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from capture_splat.resplat_adapter import (CHECKPOINT, CHECKPOINT_SHA256, SOURCE_COMMIT,
                                           inference_command, prepare, sha256)
from export_mobile_splat import export


def run(capture: Path, out: Path, root: Path) -> dict:
    capture, out, root = capture.resolve(), out.resolve(), root.resolve()
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != SOURCE_COMMIT or sha256(root / 'pretrained' / CHECKPOINT) != CHECKPOINT_SHA256:
        raise ValueError('External ReSplat source/checkpoint pin mismatch.')
    prepare(capture, out)
    command = inference_command(root, out / 'scene', out / 'inference', sys.executable)
    env = dict(os.environ, CUDA_HOME='/usr/local/cuda-12.8', MAX_JOBS='2', TORCH_CUDA_ARCH_LIST='8.6',
               WANDB_MODE='disabled', PYTHONUNBUFFERED='1')
    env['PATH'] = '/usr/local/cuda-12.8/bin:' + env.get('PATH', '')
    with (out / 'inference.log').open('x') as log:
        subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=1800)
    model = out / 'inference/gaussians.ply'
    if not model.is_file() or model.stat().st_size > 512 * 1024**2:
        raise ValueError('ReSplat did not produce a bounded Gaussian PLY.')
    receipt = export(model, out / 'model.splat')
    receipt.update(decision='hold', engine='resplat', source_commit=actual,
                   checkpoint_sha256=CHECKPOINT_SHA256,
                   coordinate_frame='upstream_context_view_aligned_not_arkit_world',
                   viewer_camera={'position': [0, 0, 0], 'rotation': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                  'fx': 1100, 'fy': 1100, 'width': 1920, 'height': 1440})
    (out / 'result.json').write_text(json.dumps(receipt, indent=2, allow_nan=False))
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--root', type=Path, default=Path('external/resplat'))
    args = p.parse_args()
    print(json.dumps(run(args.capture, args.out, args.root), indent=2))

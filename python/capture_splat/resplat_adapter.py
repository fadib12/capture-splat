"""Prepare accepted camera/image evidence for the pinned external ReSplat demo.

This adapter does not run a model, refine poses or establish room quality.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .capture_schema import iter_frames, load_capture
from .colmap_export import export_colmap_text
from .json_utils import write_json_strict

SOURCE_COMMIT = 'cae7ddc4cdbd80e05e9f5fa00f5ea02c4e9056b1'
CHECKPOINT = 'resplat-small-dl3dv-256x448-view8-548993fe.pth'
CHECKPOINT_SHA256 = '548993fede0d9536d2d914cbe51e0ebea0ad6f88c898c909e02127d59bb2be9a'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(capture: Path, out: Path) -> dict:
    capture = capture.resolve(strict=True)
    out = out.resolve()
    if out == capture or out.is_relative_to(capture) or capture.is_relative_to(out):
        raise ValueError('Output must be separate from the immutable capture.')
    if out.exists():
        raise ValueError('Use a fresh output directory.')
    manifest = capture / 'capture.json'
    if manifest.is_symlink() or manifest.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Invalid or oversized capture manifest.')
    original_manifest = sha256(manifest)
    data = load_capture(capture)
    frames = list(iter_frames(data, accepted_only=True))
    if not 8 <= len(frames) <= 512:
        raise ValueError('The initial ReSplat adapter requires 8 to 512 accepted images.')
    bindings = []
    total_bytes = 0
    seen = set()
    for frame in frames:
        relative = PurePosixPath(frame.image_path)
        if relative.is_absolute() or '..' in relative.parts or '\\' in frame.image_path or ':' in frame.image_path:
            raise ValueError('Image path must be relative and contained in the capture.')
        image = capture.joinpath(*relative.parts)
        if any(part.is_symlink() for part in [image, *image.parents] if part != capture):
            raise ValueError('Symlink image inputs are unsupported.')
        image = image.resolve(strict=True)
        if not image.is_relative_to(capture) or image in seen:
            raise ValueError('Duplicate or escaping capture image.')
        seen.add(image)
        size = image.stat().st_size
        total_bytes += size
        if not 0 < size <= 25 * 1024 * 1024 or total_bytes > 2 * 1024**3:
            raise ValueError('Image byte budget exceeded.')
        bindings.append({'source_path': relative.as_posix(), 'bytes': size,
                         'sha256': sha256(image), 'source_frame_index': frame.source_index,
                         'output_path': f'images/{frame.index:06d}{image.suffix.lower() or ".jpg"}'})
    out.mkdir(parents=True, exist_ok=False)
    summary = export_colmap_text(capture, out / 'scene')
    # Bind both sides after copying. Source edits during preparation must fail.
    for binding in bindings:
        source = capture / binding['source_path']
        copied = out / 'scene' / binding['output_path']
        if (source.stat().st_size != binding['bytes'] or copied.stat().st_size != binding['bytes']
                or sha256(source) != binding['sha256'] or sha256(copied) != binding['sha256']):
            raise ValueError('Image evidence changed during preparation.')
    if sha256(manifest) != original_manifest:
        raise ValueError('Capture manifest changed during preparation.')
    receipt = {
        'schema': 'capture_splat.resplat_preparation.v0.1', 'decision': 'hold',
        'source_manifest_sha256': original_manifest, 'frames': bindings,
        'accepted_image_count': summary['image_count'],
        'pose_source': 'arkit_prior_not_sfm_refined',
        'camera_conversion': 'opencv_c2w = arkit_c2w @ diag(1,-1,-1,1), exactly once',
        'undistortion': 'ARKit pinhole input assumption; not independently calibrated',
        'sensor_depth_supervision_applied': False,
        'external_source_commit': SOURCE_COMMIT,
        'model_runtime_executed': False,
        'context_limit': 8,
        'limitations': ['Upstream small preset selects 8 context images; full-room coverage is unproven.',
                        'Upstream PLY is aligned to a context view; it is not ARKit-world metric output.'],
        'authority': {'proposal_only': True, 'visual_quality': False, 'metric': False,
                      'collision': False, 'navigation': False},
    }
    write_json_strict(out / 'preparation_receipt.json', receipt)
    return receipt


def inference_command(root: Path, scene: Path, output: Path, python: str = 'python') -> list[str]:
    """Build argv for the pinned upstream program; never execute through a shell."""
    return [python, str(root / 'scripts/infer_colmap.py'),
            '--model_preset', 'dl3dv_8v_256x448_small',
            '--checkpoint', str(root / 'pretrained' / CHECKPOINT),
            '--scene_path', str(scene), '--images_dir', 'images',
            '--sparse_dir', 'sparse/0', '--output_dir', str(output),
            '--num_target', '8', '--render_chunk_size', '1',
            '--max_save_images', '8', '--save_images', '--save_ply', '--no_eval']

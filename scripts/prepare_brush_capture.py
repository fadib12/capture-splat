"""Adapt an immutable Capture Splat export for a pinned external Brush process.

This uses ARKit pose priors, not SfM-refined cameras. It grants no reconstruction,
metric, collision, or navigation authority. The trainer remains user-installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image

from capture_splat.capture_schema import iter_frames, load_capture
from capture_splat.ingest import ingest_capture
from capture_splat.json_utils import load_json_strict, write_json_strict


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(capture: Path, out: Path) -> dict:
    capture = capture.resolve(strict=True)
    out = out.resolve()
    if out.is_relative_to(capture):
        raise ValueError("output must be outside the immutable capture")
    out.mkdir(parents=True, exist_ok=False)
    source = load_capture(capture)
    ingest_capture(capture, out)
    dataset = out / "nerfstudio_dataset"
    transforms = load_json_strict(dataset / "transforms.json")
    bindings = []
    selected = list(iter_frames(source, accepted_only=True))
    for frame, target in zip(selected, transforms["frames"], strict=True):
        image = dataset / target["file_path"]
        with Image.open(image) as photo:
            width, height = photo.size
        intr = frame.intrinsics
        sx, sy = width / intr["w"], height / intr["h"]
        target.update(w=width, h=height, fl_x=intr["fl_x"] * sx,
                      fl_y=intr["fl_y"] * sy, cx=intr["cx"] * sx, cy=intr["cy"] * sy)
        bindings.append({"path": target["file_path"], "sha256": sha256(image),
                         "source_frame_index": frame.source_index})
    transforms["camera_model"] = "PINHOLE"
    # Preserve ARKit/OpenGL c2w. Brush's Nerfstudio reader applies the camera-axis
    # conversion itself; preflipping here would apply it twice.
    seed_path = source.get("pointcloud_preview_file")
    seed_count = 0
    if seed_path:
        seed_source = (capture / seed_path).resolve(strict=True)
        if not seed_source.is_relative_to(capture) or seed_source.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("invalid or oversized preview seed")
        preview = load_json_strict(seed_source)
        if preview.get("coordinate_frame") != "arkit_world_preview":
            raise ValueError("preview seed has an unsupported coordinate frame")
        points = preview.get("points", [])
        if len(points) > 100_000:
            raise ValueError("preview point budget exceeded")
        unique = {}
        for point in points:
            xyz = [float(point[k]) for k in ("x", "y", "z")]
            if not all(math.isfinite(v) and abs(v) < 10000 for v in xyz):
                raise ValueError("invalid seed position")
            key = tuple(math.floor(v / 0.025) for v in xyz)
            rgb = [max(0, min(255, int(point[k]))) for k in ("r", "g", "b")]
            unique.setdefault(key, (*xyz, *rgb))
        seed_count = len(unique)
        with (dataset / "init.ply").open("x", encoding="ascii", newline="\n") as stream:
            stream.write("ply\nformat ascii 1.0\n" + f"element vertex {seed_count}\n")
            stream.write("property float x\nproperty float y\nproperty float z\n")
            stream.write("property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
            for point in unique.values():
                stream.write(" ".join(map(str, point)) + "\n")
        transforms["ply_file_path"] = "init.ply"
    write_json_strict(dataset / "transforms.json", transforms)
    receipt = {"schema": "capture_splat.brush_preparation.v0.1",
               "source_capture_sha256": sha256(capture / "capture.json"),
               "camera_to_world_axes": "arkit_opengl_x_right_y_up_z_back",
               "pose_source": "arkit_prior_not_sfm_refined", "frames": bindings,
               "seed_points": seed_count, "seed_role": "sensor_preview_initialization_only",
               "transforms_sha256": sha256(dataset / "transforms.json"),
               "authority": {"proposal_only": True, "quality": False, "metric": False,
                             "collision": False, "navigation": False}}
    if seed_count:
        receipt["seed_sha256"] = sha256(dataset / "init.ply")
    write_json_strict(out / "preparation_receipt.json", receipt)
    return {"dataset": str(dataset), "frames": len(bindings), "seed_points": seed_count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.capture, args.out), indent=2))

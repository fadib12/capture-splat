"""Export a finite Gaussian PLY to the MIT viewer's documented 32-byte format."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
from plyfile import PlyData

from capture_splat.json_utils import write_json_strict


def export(source: Path, target: Path) -> dict:
    vertices = PlyData.read(source)["vertex"].data
    required = ["x", "y", "z", "scale_0", "scale_1", "scale_2", "opacity",
                "f_dc_0", "f_dc_1", "f_dc_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    if not set(required).issubset(vertices.dtype.names or ()) or len(vertices) > 2_000_000:
        raise ValueError("unsupported Gaussian PLY or mobile point budget exceeded")
    arrays = {name: np.asarray(vertices[name], dtype=np.float64) for name in required}
    if not len(vertices) or not all(np.isfinite(value).all() for value in arrays.values()):
        raise ValueError("empty or non-finite Gaussian input")
    positions = np.column_stack([arrays[k] for k in ("x", "y", "z")])
    scales = np.exp(np.column_stack([arrays[f"scale_{i}"] for i in range(3)]))
    rotations = np.column_stack([arrays[f"rot_{i}"] for i in range(4)])
    norms = np.linalg.norm(rotations, axis=1)
    if not np.isfinite(scales).all() or (norms <= 1e-10).any():
        raise ValueError("invalid Gaussian scale or rotation")
    rotations /= norms[:, None]
    colors = np.column_stack([0.5 + 0.28209479177387814 * arrays[f"f_dc_{i}"] for i in range(3)])
    alpha = 1 / (1 + np.exp(-np.clip(arrays["opacity"], -80, 80)))
    order = np.argsort(-np.prod(scales, axis=1) * alpha, kind="stable")
    packed = np.empty(len(vertices), dtype=np.dtype([
        ("position", "<f4", (3,)), ("scale", "<f4", (3,)),
        ("rgba", "u1", (4,)), ("rotation", "u1", (4,))]))
    packed["position"] = positions[order]
    packed["scale"] = scales[order]
    if not np.isfinite(packed["position"]).all() or not np.isfinite(packed["scale"]).all() or (packed["scale"] <= 0).any():
        raise ValueError("float32 export overflow or underflow")
    packed["rgba"] = (np.column_stack([colors, alpha])[order] * 255).clip(0, 255).astype(np.uint8)
    packed["rotation"] = (rotations[order] * 128 + 128).clip(0, 255).astype(np.uint8)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(packed.tobytes())
    receipt = {"schema": "capture_splat.mobile_splat_export.v0.1", "splat_count": len(vertices),
               "bytes": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
               "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "format": "antimatter15_splat_32byte", "coordinate_transform": "identity",
               "color": "spherical_harmonics_dc_only", "higher_order_sh_preserved": False,
               "authority": {"proposal_only": True, "visual_quality": False, "metric": False,
                             "collision": False, "navigation": False}}
    write_json_strict(target.with_suffix(".splat.json"), receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(export(args.input, args.out))

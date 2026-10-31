"""Check published repository content, without treating private working data as commits."""
from pathlib import Path
import subprocess


def test_no_generated_3d_artifacts_committed() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = {".ply", ".las", ".laz", ".npy", ".npz", ".ipa", ".xcarchive"}
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    offenders = [name for name in tracked.decode("utf-8").split("\0")
                 if name and Path(name).suffix.lower() in forbidden]
    assert offenders == []

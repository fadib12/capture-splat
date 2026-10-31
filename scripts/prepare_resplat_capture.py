"""Adapt a saved scan to the existing pretrained ReSplat input contract."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from capture_splat.resplat_adapter import prepare

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.capture, args.out)
    print(json.dumps({'decision': report['decision'], 'prepared_images': report['accepted_image_count'],
                      'model_runtime_executed': False}, indent=2))

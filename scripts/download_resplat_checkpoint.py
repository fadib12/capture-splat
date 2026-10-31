"""Download a pinned pretrained ReSplat checkpoint without changing an existing model."""
import argparse
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from capture_splat.resplat_adapter import profile_config, sha256

MODEL_REVISION = 'c69ad72a49b8358ac46599cf74468090425ed9c7'

def download(profile: str, directory: Path) -> Path:
    config = profile_config(profile)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / config['checkpoint']
    if target.exists():
        if sha256(target) != config['sha256']:
            raise ValueError('Existing checkpoint does not match the pinned SHA-256; it was retained.')
        return target
    temporary = target.with_suffix('.download')
    url = f"https://huggingface.co/haofeixu/resplat/resolve/{MODEL_REVISION}/{config['checkpoint']}"
    # Exclusive creation protects an earlier interrupted download for inspection.
    with temporary.open('xb') as output:
        digest = hashlib.sha256()
        total = 0
        with urlopen(url, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 1024**3:
                    raise ValueError('Checkpoint exceeds the download budget.')
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != config['sha256']:
            raise ValueError('Checkpoint SHA-256 mismatch; the download was retained for inspection.')
    temporary.rename(target)
    return target

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('small', 'detail'), default='detail')
    parser.add_argument('--directory', type=Path, default=Path('external/resplat/pretrained'))
    args = parser.parse_args()
    print(download(args.profile, args.directory))

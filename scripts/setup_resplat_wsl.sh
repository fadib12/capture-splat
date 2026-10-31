#!/usr/bin/env bash
# Run as root only inside the dedicated CaptureSplat Ubuntu 24.04 WSL distro.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3.12-venv python3-dev build-essential git curl ca-certificates ninja-build libgl1 libglib2.0-0
if ! command -v /usr/local/cuda-12.8/bin/nvcc >/dev/null 2>&1; then
    curl -fL --retry 3 https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb -o /tmp/capture-splat-cuda-keyring.deb
    dpkg -i /tmp/capture-splat-cuda-keyring.deb
    apt-get update -qq
    # Compiler and headers only; WSL uses the existing Windows display driver.
    apt-get install -y --no-install-recommends cuda-nvcc-12-8 cuda-cudart-dev-12-8 cuda-cccl-12-8
fi
python3.12 -m venv /opt/capture-splat-resplat
PY=/opt/capture-splat-resplat/bin/python
"$PY" -m pip install --upgrade pip setuptools wheel ninja
"$PY" -m pip install torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128
REPO=$(cd "$(dirname "$0")/.." && pwd)
"$PY" -m pip install -r "$REPO/external/resplat/requirements.txt"
export CUDA_HOME=/usr/local/cuda-12.8
export PATH="$CUDA_HOME/bin:/opt/capture-splat-resplat/bin:$PATH"
export TORCH_CUDA_ARCH_LIST=8.6
export MAX_JOBS=2
"$PY" -m pip install --no-build-isolation 'git+https://github.com/nerfstudio-project/gsplat.git@v1.5.3'
(cd "$REPO/external/resplat/src/model/encoder/pointops" && "$PY" -m pip install --no-build-isolation .)
"$PY" -c 'import torch,gsplat,pointops; assert torch.cuda.is_available(); print(torch.__version__, gsplat.__version__, torch.cuda.get_device_name())'

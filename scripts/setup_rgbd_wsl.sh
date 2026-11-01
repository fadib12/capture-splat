#!/usr/bin/env bash
set -euo pipefail
# Reuses the pinned Torch/gsplat packages read-only; new dependencies stay isolated.
# The WSL distro must already live on the selected data drive.
apt-get update -qq
apt-get install -y -qq ffmpeg
python3.12 -m venv /opt/capture-splat-rgbd
PY=/opt/capture-splat-rgbd/bin/python
printf '%s\n' /opt/capture-splat-resplat/lib/python3.12/site-packages > /opt/capture-splat-rgbd/lib/python3.12/site-packages/resplat-shared.pth
export MAX_JOBS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 CUDA_HOME=/usr/local/cuda-12.8 TORCH_CUDA_ARCH_LIST=8.6
export PATH=/usr/local/cuda-12.8/bin:$PATH
for include in /opt/capture-splat-resplat/lib/python3.12/site-packages/nvidia/*/include; do
 if [ -d "$include" ]; then export CPATH="$include${CPATH:+:$CPATH}"; fi
done
$PY -m pip install viser 'tyro>=0.8.8,<1' tensorboard tensorly splines scikit-learn 'opencv-python==4.10.0.84' 'numpy<2' 'imageio[ffmpeg]' 'torchmetrics[image]' pyyaml
$PY -m pip install --no-deps 'git+https://github.com/rmbrualla/pycolmap@cc7ea4b7301720ac29287dbe450952511b32125e' 'git+https://github.com/nerfstudio-project/nerfview@4538024fe0d15fd1a0e4d760f3695fc44ca72787'
$PY -m pip install --no-build-isolation 'git+https://github.com/rahul-goel/fused-ssim@328dc9836f513d00c4b5bc38fe30478b4435cbb5'
$PY -c 'import torch,gsplat,fused_ssim,pycolmap; print(torch.__version__,gsplat.__version__,torch.cuda.is_available())'

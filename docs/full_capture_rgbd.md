# Full-capture private PC reconstruction

The new app sends a finalized capture inventory through authenticated pinned TLS,
using resumable 4 MiB chunks. JPEGs, video, frame calibration/trajectory, depth,
confidence, captured mesh/reports, masks, RoomPlan and IMU/GPS files are preserved.
Local finalization and Manual Export remain independent. This is a single-operator
PC executor, not production World Studio job or publication authority.

Transfer limits are 4,096 files, 4 GiB total, 2 GiB per file and six relative path
components. Every file has an original-byte SHA-256 binding, including capture.json.
Traversal, absolute paths, casefold aliases, symlinks/junctions and Windows device
names are rejected. A full RGB-D job requires video, indexed camera metadata,
depth/confidence and all referenced assets; it never silently falls back to the
older eight-photo ReSplat job. V1 remains available to older clients.

## Reconstruction consumption

The external Apache-2.0 gsplat v1.5.3 trainer is pinned at
`937e29912570c372bed6747a5c9bf85fed877bae`. Its normalized-LF trainer SHA-256 is
`79319e1cd7404e4d1ba0c425634235c39e6054f0643b904a01feea6179462c05`.
No trainer implementation is vendored or recreated. Independent data/AST adapters
bind this exact source and change only calibrated Capture Splat ingestion,
measured sensor sampling, bounded metric loss, empty-depth guards, finite-loss
checks and single-process loading. Upstream source stays unchanged.

- Preparation retains accepted RGB-D keyframes and adds sharp, pose-matched video
  views across the complete clip. The initial target is 300 images.
- Source depth and confidence initialize Gaussians. A finite, report-checked ARKit
  mesh contributes additional seed points in the shared ARKit world frame. Mesh
  colors are projected from captured views; unsupported colors remain proposals.
- The color objective uses the prepared training views. Video-only views have no
  invented sensor depth.
- Confidence >=1, finite 0.05..20 m optical-Z measurements and valid masks select
  up to 4,096 calibrated depth rays per RGB-D training image. Their pixels use the
  actual training intrinsics and depth-grid dimensions.
- Measured rays contribute a metric Huber depth objective (beta 0.1 m, weight 0.1)
  alongside the existing color objective. Empty or implausible rendered rays are
  excluded; non-finite total loss blocks immediately. The ledger records actual
  differentiable depth-loss steps and the actual number of sensor samples used.
- Camera axes convert ARKit/OpenGL c2w to OpenCV once. Trainer world normalization
  and pose optimization are disabled for this initial sensor-aligned lane.
- MCMC refinement is capped at 200,000 Gaussians, with a 200-step refinement floor
  for shortened rungs. Initial Gaussian scale is half the local neighbor scale.
  Those limits are settings, not a general 8 GB capacity or quality claim.

Raw IMU/GPS and RoomPlan are retained context, not direct training losses. ARKit
poses already carry VIO motion tracking. Person/valid masks filter sensor rays;
this initial lane does not claim complete dynamic-object removal or normal loss.

The full-SH3 PLY, source/preparation hashes and training-consumption receipt are
bound before delivery. OpenGL world-camera metadata preserves the capture frame
for the existing full-screen offline Spark viewer. Missing/unobserved surfaces
remain uncertain; no diffusion-invented wall or fallback collision geometry is
introduced. Finite output or viewer load does not prove visual quality, metric
scale, room completeness, collision, navigation or physics authority.

## Run and verify

Install the pinned external gsplat source under `external/gsplat-rgbd` and run
`scripts/setup_rgbd_wsl.sh` inside the existing dedicated CaptureSplat WSL distro.
Then use a new output directory:

```bash
/opt/capture-splat-rgbd/bin/python scripts/run_rgbd_worker.py \
  --capture captures/my_room --out runs/my_rgbd_room
```

A completed result must include `preparation.json`, `training-ledger.json`,
`training-consumption.json`, a finite bounded `inference/gaussians.ply`, held-out
renders/statistics and `result.json`. Inspect the interior and compare fixed
source cameras before any quality promotion. Initialization uses the capture geometry and projected colors, including the
optimization-validation cameras; that split is not an independent capture test.
Sensor consumption is separate from a demonstrated benefit versus an RGB-only
control.

Enable the protocol with `scripts/start_resplat_pc.py --host PRIVATE_IP --enable-rgbd`
only after a real GPU execution has created the local readiness evidence under
`runs/pc-bridge/`. The app needs version 0.1.8 (10) or newer. Retain the existing
private TLS pairing; credentials never enter Git or the IPA. Keep the app open
until upload, PC reconstruction and the checked result download finish.

This path uses per-room optimization in an existing trainer. It does not turn
ReSplat into an RGB-D pretrained model or promise reconstruction in seconds.

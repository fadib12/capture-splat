# Local Brush training and the mobile splat viewer

Capture Splat can adapt an existing finalized capture for a pinned external
Brush trainer, then export its result for the bundled offline mobile viewer.
This is a local PC workflow, not an on-phone trainer or a hosted cloud service.
World Studio remains the owner of production jobs, publication and promotion.
Manual export does not depend on pairing or live transfer.

## Installed components

- Trainer: [ArthurBrussee/brush](https://github.com/ArthurBrussee/brush), Apache-2.0,
  official Windows release v0.3.0, source commit
  `3edecbb2fe79d3e2c87eeab85b15e0b1dd10d486`. Keep it under ignored `external/`.
  Verify the Windows ZIP against its published SHA-256 sidecar before extraction.
- Viewer: [antimatter15/splat](https://github.com/antimatter15/splat), MIT,
  pinned commit `ba182b51b7c2ad5738cdd6741cd63336d27470fb`. Its renderer is bundled
  under the iOS resources with the license, source attribution and modification
  notes. The default remote demo and sample cameras have been removed.

The external trainer is not imported or vendored into the capture app. The
existing VkSplat/gsplat reconstruction commands and frozen COLMAP/HLOC lane
remain available. Brush is an additional local experimental adapter, not a
same-input-promoted replacement for the product baseline.

## Prepare and train a saved scan

Extract the export ZIP first, retaining the original. In PowerShell from the
repository root:

```powershell
.venv/Scripts/python.exe -m pip install -e '.[dev,brush]'
scripts/train_room_windows.ps1 -Capture captures/my_scan
```

The script requires the verified official trainer at
`external/brush-v0.3.0/bin/brush_app.exe`. It creates a new run directory, adapts
the accepted RGB-D photographs and per-frame camera intrinsics, and preserves
their ARKit/OpenGL camera-to-world poses. Brush's pinned Nerfstudio loader
performs its own camera basis conversion; the adapter must not preflip poses.
These are ARKit priors, not SfM-refined camera results.

The adapter can initialize from the bounded recorded LiDAR preview, voxelizing
only that derived seed. Seed use is initialization evidence, not proof of
dedicated depth-loss supervision. Raw RGB-D and the source package are not
modified. Continuous video supplements are not consumed by this adapter.

The default trial requests 15,000 steps, a 700,000-Gaussian cap, 1280-pixel maximum
image dimension, and a held-out view every eight frames. These are explicit
trial settings, not a capacity or speed guarantee for 8 GB GPUs or other devices.
The script stops if the trainer fails or the expected output is missing.

Outputs include the raw SH3 Gaussian PLY, an alpha-pruned PLY, held-out renders,
preparation/source bindings, the command record, and `viewer/model.splat`.
Pruning removes Gaussians below alpha 12/255 and is viewer hygiene only.
Do not infer visual-quality improvement from a smaller or finite output.

The compact mobile format preserves position, Gaussian scale/rotation, opacity
and DC color. Higher-order spherical harmonics remain in the PLY and are not
present in `.splat`. Keep the PLY for further training, editing, and comparisons.

## Open the result

On the phone, choose **More -> Open trained splat**, or **Open trained splat**
on the saved-scan screen, then select `model.splat` from Files. The app copies
that explicitly selected result to its own library and can reopen the latest
import. The import validates the row layout, a 64 MB file limit, finite geometry,
positive scales and nonzero rotation before opening the offline WebGL viewer.
The camera pauses while selecting or viewing a trained result.

For desktop viewing, serve the generated `viewer/` directory with a local HTTP
server and open its loopback address. The viewer accepts touch, mouse and
keyboard navigation. The model remains private unless the owner shares it;
the bundled viewer has no default remote-model fetch.

Finishing a scan does not mean training completed: the app offers export for
the PC trainer and import of a real trained result. It does not fabricate a
processing job, automatically upload captures, or substitute a point-cloud
preview for a Gaussian model. Automatic production job integration is separate
World Studio work.

## Minimal capture interface and camera selection

Version 0.1.4 (5) removes the default scan card, map, counters and explanatory
overlays. The ARKit wireframe remains a recording-only surface observation aid;
full diagnostics remain behind Capture details. Saved scans offer export and
opening a trained splat instead of a cloud-setup screen.

The configuration selects a rear ultra-wide video format only if ARKit exposes
one in `ARWorldTrackingConfiguration.supportedVideoFormats`. It otherwise uses
ARKit's supported primary camera. The idle lens badge and manifest lens field
describe the selected camera; there is no synthetic 0.5x label. Calibrated depth
and physical camera support must be checked on the device before claiming an
ultra-wide RGB-D capture path. The existing tracking/depth/quality gates remain.

## Evidence limits

Inspect fixed-camera renders against the source and bind them to the exact
unpruned training PLY. Held-out metrics from that PLY do not automatically
validate a pruned or DC-only mobile export. ARKit prior drift, mirrors, moving
people and missing viewpoints can leave soft or incorrect regions. Viewer
load, finite output and job completion do not establish visual quality, metric
scale, collision safety or navigation authority. Keep the result proposal-only
until its corresponding evidence gates pass.

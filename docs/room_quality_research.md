# Room reconstruction quality: research and proposed validation plan

Research date: 2026-10-31. Status: **proposal / hold**. This document does not
change capture, training, rendering, publication or iPhone builds. Recommendations
below are engineering hypotheses until same-input tests and physical-device
acceptance pass. Published paper results are not results on this application.

## Decision

Keep the existing capture and gsplat foundation. Prioritize renderer parity,
camera alignment, source resolution, measured geometry and coverage before adding
another generative enhancer. The objective is a room that remains convincing
while moving through its supported interior viewing area.

No inspected method establishes accurate, detailed reconstruction of every room
from arbitrary phone movement. A general room product needs adaptive capture,
explicit quality gates and targeted recapture. Unobserved surfaces must remain
unknown; generated completion is a separately labelled proposal. For realtor
delivery, do not silently change windows, doors, fixtures, furniture or finishes.

## What the current implementation actually does

These findings come from the local wrappers, pinned dependencies and retained
trial reports; they are not inferred from a screenshot.

| Area | Observed state | Implication to investigate |
| --- | --- | --- |
| Input | Original room photographs are 1920 x 1440 | A larger render cannot create true additional captured detail |
| Training | Initial RGB-D trial uses data_factor=2, 3,000 steps, 200k MCMC ceiling | Training images are 960 x 720; a conservative trial, not a final quality recipe |
| Geometry | Confidence-filtered depth loss and depth/mesh initialization are consumed | Useful measured constraints already exist; do not discard them for a photo-only replacement |
| Cameras | Initial sensor-aligned trial disables pose optimization | ARKit camera errors remain a possible source of smearing; not yet quantified |
| Appearance | Bilateral-grid and appearance optimization disabled | Exposure/white-balance variation remains a possible confound |
| Training renderer | Classic rasterization | Must match export/viewer behavior before judging new models |
| Viewer | Spark 2.4.0, radial sorting, default blur parameters | Potential training/viewer mismatch requiring an A/B test |
| Delivery | Full SH3 PLY, 64 MiB and 2M-vertex native limits; LoD disabled | A larger master model requires an explicit delivery design, not removal of safeguards |
| Generalization | One operator room plus older benchmark evidence | Insufficient evidence for an all-room quality claim |

Evidence: `scripts/gsplat_rgbd_entry.py`, `docs/full_capture_rgbd.md`,
`apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer/main.source.js`,
`docs/anchorsplat_trial.md`, and `docs/freefix_trial.md`.

The experiments do not establish a compelling generative repair stage. AnchorSplat
regressed the matched-view averages. FreeFix changed 22.8793 to 22.9370 mean PSNR
and 0.7505 to 0.7583 mean SSIM across eight optimization-held-out views: a small
proxy improvement with visible remaining softness and some distortions. Its first
repaired intermediate image smoothed away a wall picture. These are not independent
capture-validation scores because the baseline initialization used broader capture
geometry. Neither trial establishes accurate missing-surface recovery.

## 1. Separate renderer error from reconstruction error

Pinned Spark defaults are preBlurAmount=0, blurAmount=0.3, focalAdjustment=1 and
sortRadial=true. Our wrapper leaves these blur defaults implicit, while the current
trainer uses classic rasterization. This is a **mismatch candidate**, not a proven
explanation for all blur. Spark documents different blur handling for classic and
antialiased splats, and a tradeoff between radial and depth sorting. See the
[renderer contract](https://sparkjs.dev/docs/spark-renderer/).

First proposed experiment: keep one PLY immutable and render identical cameras in
gsplat and Spark. Match full intrinsics, image size, principal point, camera axes,
background, SH degree, exposure and color encoding. A viewer's convenient default
field of view is not a calibrated reference camera.

Test classic-compatible and current blur settings crossed with depth/radial
sorting. Measure raw canvases, not screenshots containing interface elements.
Inspect a fixed translation/rotation path, silhouette overlap, apparent surface
thickness, close-up behavior and opacity. Change one setting family at a time.
Do not sharpen by shrinking splats until holes appear or hide defects using blur.

Mip-Splatting addresses distance/sampling artifacts through both a 3D frequency
constraint and a 2D filter. Its result is not equivalent to turning on generic
WebGL antialiasing. See the [paper/project](https://niujinshuchong.github.io/mip-splatting/).
gsplat's antialiased mode applies view-dependent opacity compensation; this alone
does not demonstrate the complete 3D smoothing component. Training, export and
viewer filtering must pass one contract together. See
[gsplat rasterization](https://docs.gsplat.studio/main/apis/rasterization.html).

## 2. Correct camera alignment before spending more Gaussians

Keep raw ARKit trajectory, calibration, timestamps and uncertainty. Compare them
against feature-based reconstruction using the frozen external HLOC/COLMAP lane.
The pinned Spirula built-in SfM candidate remains eligible only after the existing
same-input evidence gate; it remains an external process with its own terms.

Use temporal neighbors and verified loop-closure candidates; expand matching only
where registration or overlap is weak. Reject geometrically inconsistent matches
in repetitive doors, tiles and mirrors. SIFT is a conservative control; ALIKED +
LightGlue is a candidate for more difficult image pairs, not a guaranteed cure for
blank walls. The official [COLMAP guidance](https://colmap.github.io/faq.html)
describes feature/camera choices. [HLOC](https://github.com/cvg/Hierarchical-Localization)
provides the integration path; [LightGlue](https://github.com/cvg/LightGlue)
publishes matcher weights, with extractor-specific terms.

Important constraints:

- COLMAP's documented pose-prior mapper constrains positions. It is not an
  automatic full ARKit orientation/depth/IMU fusion pipeline.
- Keep calibrated per-frame intrinsics; share them only when the camera settings
  justify sharing. Avoid treating autofocus or lens changes as identical cameras.
- Preserve metric evidence separately from an SfM coordinate frame. Verify the
  world alignment using full rotations and reliable sensor correspondences.
- Convert OpenGL c2w to OpenCV exactly once, then invert where required.
- If poses receive individual corrections, reproject raw depth using corrected
  poses and rebuild the seed. A single global transform cannot repair local drift
  in the original ARKit mesh. Retain that mesh as original evidence, not a
  fabricated corrected measurement.
- Joint image/depth pose refinement is an additional adapter/research task.
  Do not imply stock COLMAP consumes our depth, normals or raw IMU automatically.

Acceptance must include registration coverage and weak-frame tails, reprojection
residuals, loop consistency, depth consistency and scale evidence. A low mean
reprojection error alone is insufficient.

## 3. Make capture coverage predict usable close-up detail

Preserve the one-record-button experience. Internally select useful views using
tracking, sharpness, exposure, parallax, overlap and sensor confidence. Keep the
continuous indexed video as recovery evidence. More frames without useful
viewpoint changes can simply reinforce the same blur.

Proposed guidance should distinguish:

- Surface observed by LiDAR.
- Surface seen sharply in RGB from enough separated views.
- Surface seen at sufficient image scale for the desired viewing distance.
- Surface hidden, reflective, moving or uncertain.

These are different signals. A bright LiDAR map does not establish detailed color
coverage. During recording, show one actionable prompt, such as revisit a corner,
lower the camera toward the floor, or approach a poorly resolved feature. Keep
diagnostic overlays optional and guidance off before recording.

Capture an overview plus overlapping views of important surfaces at closer
distances. Include high/low surfaces and both spatial sides of relevant doorways.
Return to recognizable features for loop evidence. Use the widest calibrated
ARKit format that preserves the agreed LiDAR path; do not silently substitute an
uncalibrated ultra-wide video feed. Preserve local finalization/manual export if
network or GPU work fails.

Higher resolution and frame rate are separate choices. Sharp, well-exposed views
are the useful objective. Sustain the device's thermal budget; a 4K capture option
must earn qualification on the actual iPhone and cannot upgrade old photographs.

## 4. Train detail while retaining measured geometry

Proposed controlled ladder:

1. Freeze the prepared input and validation split. Establish renderer parity.
2. Compare corrected poses against the sensor-aligned control.
3. Compare current resolution against full original 1920 x 1440 supervision,
   optionally using a progressive-resolution schedule.
4. Keep confidence-filtered measured depth constraints. Evaluate depth-derived
   normals separately, with discontinuity, incidence and confidence rejection.
5. Test exposure correction separately, retaining raw and corrected metrics.
6. Compare MCMC against an AbsGrad/screen-size-aware densification candidate.
7. Evaluate antialiased training/export/viewer behavior as a matched configuration.

The existing [gsplat trainer](https://github.com/nerfstudio-project/gsplat/blob/main/examples/simple_trainer.py)
already exposes camera, appearance and resolution controls. The pinned installed
version must be checked before using flags shown by current-main documentation.
Its local low-level renderer includes UT/eval3d paths, but they are not enabled by
our wrapper. There is no need to rewrite a trainer from scratch.

[DN-Splatter](https://github.com/maturk/dn-splatter) is a relevant indoor/smartphone
reference for combining depth and normals. Its Apache-2.0 core does not establish
terms for every optional model/dependency. Prefer measured depth-derived normals
for the first controlled test; validate any learned prior separately.

[gsplat strategies](https://docs.gsplat.studio/main/apis/strategy.html) distinguish
splitting, duplication, pruning and MCMC allocation. Do not blindly combine MCMC
and DefaultStrategy/AbsGrad settings. Tune each complete strategy against the
same input, resource budget and view set. Enlarging the Gaussian cap is not proof
that small splats are supported by enough captured evidence.

Use the existing 3k -> 7k -> 15k -> 30k evidence ladder where appropriate; do not
treat those counts as universal completion thresholds. Stop or hold when tail
views regress, geometry drifts, inputs are weak or resource limits are reached.

## 5. Scale the master model and mobile delivery separately

Retain an uncompressed master and its camera/renderer metadata. Test bounded
mobile packages against it, including near-view quality and SH preservation.
The current full-float SH3 layouts use roughly 236-248 bytes per Gaussian, so
64 MiB accommodates only about 270k-284k Gaussians before headers, despite the
separate 2M-vertex guard. File encoding is already a practical detail-budget limit.
SPZ is a candidate compressed transport, not a quality enhancer; format/source
license and coordinate behavior are documented by [SPZ](https://github.com/nianticlabs/spz).
Verify decoded attributes and rendered results before promotion.

For larger rooms, investigate spatial chunks and streaming LoD rather than
loading an unbounded master into the phone. Compression does not remove decoded
GPU-memory costs. Current native limits stay enforced until a new bounded import,
decode, paging and lifecycle contract passes physical iPhone tests. Never infer
collision safety from viewer movement limits or a Gaussian envelope.

## 6. Handle different room types explicitly

These are proposed routing/acceptance rules, not implemented automatic detection.

| Room condition | Primary response | Honest failure behavior |
| --- | --- | --- |
| Furnished bedroom/living room | Multi-distance sharp RGB, refined poses, RGB-D training | Request missing coverage for occluded surfaces |
| Empty/plain room | Stronger reliable depth/normal evidence and corner overlap | Hold unsupported fine texture; do not manufacture it |
| Kitchen/bathroom/glass | Identify reflective regions; avoid using reflected features as stable geometry | Flag uncertain mirrors, glass and glossy fixtures |
| Dark room | Improve actual lighting/exposure and hold steady for sharp views | Ask for recapture when useful detail is absent |
| Bright windows/backlighting | Check clipped highlights and exposure consistency | Preserve uncertainty outside/through the window |
| Clutter/people/pets | Static-scene capture, valid masks and temporal outlier checks | Hold dynamic regions or rescan them |
| Large room/corridor | Verified loop closure, more useful baselines and spatial packaging | Detect disconnected or drifting reconstructions |
| Connected rooms | Doorway overlap, shared pose graph, room-wise QA | Hold unsupported connections; avoid invented geometry |

For severe projection/distortion cases, [NVIDIA 3DGRUT](https://github.com/nv-tlabs/3dgrut)
is an Apache-2.0 candidate family. Its rendering formulation and hardware/runtime
requirements need separate qualification and viewer parity; an ordinary exported
PLY is not evidence of equivalent appearance in Spark. This is later-stage
research, not a prerequisite for fixing today's baseline.

## 7. Prove generalization with a room matrix

Keep the repository's benchmark policy: Lego smoke, the completed Playroom
control, and complete Bonsai images_2 as the active 360 quality lane. Keep the
local iPhone lane separate. Source, licence and reference completeness must pass
before running a benchmark.

Propose at least twelve consented real-room cases spanning the table above,
including difficult failures, plus repeat captures and an untouched evaluation
subset. This is an initial engineering matrix, not evidence for universal support.
[ScanNet++](https://scannetpp.mlsg.cit.tum.de/scannetpp/) has an iPhone NVS track
with DSLR reference imagery and is relevant external evaluation; obtain and obey
its dataset terms rather than assuming downloadable data is commercially free.

For future independent room evaluation, acquire reference views in a separate
session and exclude their RGB, depth and derived geometry from training, seeds,
meshes and generated supervision. Document how reference cameras are localized.
Do not relabel the current optimization-held-out split as independent testing.

Each case needs:

- Fixed original-source cameras and physically captured near/medium/far views.
- A repeatable walk path with lateral motion, approach/retreat, corner views and
  rotation, including narrow doorway transitions where applicable.
- Raw source/render comparisons, PSNR/SSIM/LPIPS on valid reference pixels,
  per-view tails and feature crops. Report reflective/occluded exclusions.
- Depth/normal residuals on reliable measured regions, Gaussian size/outlier
  diagnostics and observed-surface coverage. These do not prove unseen geometry.
- Temporal stability checks for popping, ghosting and changing surface appearance.
  Evaluate motion using valid depth reprojection; do not penalize legitimate
  parallax or reflections as if pixels should stay identical.
- iPhone load/interaction tests, decoded CPU/GPU memory, frame-time distribution,
  thermal behavior and result recovery after interruption.

Freeze numeric promotion thresholds after baseline/noise characterization, before
evaluating candidate improvements. Require acceptable worst-view behavior and
property-feature fidelity, not just a better average. Use promote/hold/reject per
named claim and retain the previous accepted artifact on regression.

## 8. Implementation order after review

| Priority | Proposed work | Evidence required before moving on |
| --- | --- | --- |
| P0 | Calibrated gsplat/Spark parity and movement diagnosis | Same PLY/cameras; isolate blur/filter/sort/color effects |
| P1 | Refined camera evidence and full-source-resolution ladder | Better source alignment and near-view tails without depth/scale regression |
| P2 | Measured normals, appearance compensation and densification A/Bs | Per-room improvements under fixed memory/quality budgets |
| P3 | Coverage-driven capture/recapture guidance | Physical device demonstrates accurate actionable prompts |
| P4 | Bounded compressed/streamed mobile delivery | Master/package visual parity and iPhone acceptance |
| P5 | Optional generative proposal lane | Measurable benefit without silently altering property features |

A short deadline can support an initial diagnostic and a reviewed pilot. It
cannot establish all-room robustness, an App Store launch schedule or a universal
4K guarantee. The immediate recommended experiment is P0, followed by P1, while
preserving the current working capture/export path. No proposed change in this
document has been implemented by the research itself.

# Room detail implementation evidence

This is a private PC candidate lane. Local iPhone finalization and Projects ->
Manual Export remain available independently. Finished models remain proposals;
these checks do not establish collision, navigation, physical accuracy or
all-room visual quality.

## 1. Viewer parity

The exact bundled Spark 2.4.0 viewer was compared against gsplat renders of the
same SH3 model at eight calibrated cameras. The selected classic/depth profile
uses pre-blur 0.3, post-blur 0, and depth sorting. Mean/worst PSNR against the
trainer were 42.197/41.064 dB, versus 35.691/32.535 using the previous radial
profile. No browser JavaScript errors occurred. This isolates renderer
consistency; it is not source-photo quality improvement. The local report is
`runs/viewer-parity-v2/parity.json`; generated evidence stays outside Git.

Reproduce with `scripts/check_viewer_parity.py`, a fixed comparison report,
its model, a fresh output directory and an installed Chrome executable. The
optional diagnostic hook accepts calibrated OpenGL camera-to-world matrices;
it is disabled in native app pages.

## 2. Camera alignment

`rgbd_alignment.py` uses SIFT matches and high-confidence measured depth to
anchor PnP in the existing ARKit world. It excludes every eighth image from
anchor images, reserves every fourth correspondence for a separate acceptance
check, requires a distributed consensus and rejects corrections exceeding
25 cm or 8 degrees. Held matches must improve at least 20%, with median error
at most 3 pixels. No correction is applied to insufficient or rejected views.
This conservative local adapter is not global bundle adjustment and does not
replace the frozen external HLOC/COLMAP control.

The first room accepted 36/300 corrections. Among accepted views, the median
held-match error changed from 2.898 to 1.659 pixels; largest translation change
was 0.03244 m. Initialization is rebuilt from corrected measured RGB-D rays;
the unchanged ARKit mesh is excluded from this seed. The input capture and
original model remain untouched. Geometry from the same capture means this is
not independent capture validation or metric authority.

## 3. Full-resolution training

The private RGB-D worker now defaults to data factor 1 (original prepared RGB,
up to 3840 pixels in the preparation policy), 7,000 steps and a bounded
200,000-Gaussian MCMC target. This is full source resolution for this existing
1920x1440 scan, not a claim of newly captured 4K texture. `--data-factor 2` and
`--no-camera-refinement` preserve explicit control lanes. Separate factor-named
caches are dimension checked. Camera alignment, source images, depth evidence
and initialization are hash bound before training; consumed image and depth
bindings are checked again before publishing a candidate.

The completed same-input candidate under `runs/rgbd-room-193312-fullres-v1`
trained 7,000 steps on 262 training images at 1920x1440, consumed 8,922,869
measured depth samples over 2,186 depth-loss steps, and retained 200,000
Gaussians in a 47,201,477-byte SH3 PLY. The exact consumed cache pixels were
checked against the declared resize of each source image before publication.
Initialization used 60,878 corrected RGB-D seed points and zero unchanged mesh
points. Actual trainer consumption binds the alignment report.

At eight identical original cameras and full source resolution, mean PSNR
changed from 22.737 to 23.099 dB and SSIM from 0.77986 to 0.78915. Six views
improved; two regressed (-0.450 and -0.272 dB). Worst-view PSNR changed from
20.409 to 20.434 dB. Visual inspection found some clearer furniture and rug
edges, but soft door panels remain. This is a modest same-room improvement,
not all-angle sharpness, a 4K capture claim or universal room validation.
The result remains `hold` with visual/metric/collision/navigation authority
false. The original is retained both in the comparison and in the archived
PC job. Camera refinement, initialization and training changed together;
this trial does not isolate their individual contributions.

`compare_room_detail.py` produces the fixed-camera report and source-pixel
crops; `publish_room_detail.py` creates the offline comparison. Both keep
raw images, models and generated reports outside Git.

## 4. Capture guidance

A bounded 4,000-cell, 25-cm observation grid consumes at most 48 high-confidence
LiDAR rays per accepted saved RGB-D view. A second viewpoint needs at least
15 cm camera translation and five degrees ray change. Repeated stationary
frames do not add support. Upward/downward counters describe viewing directions,
not detected complete ceiling/floor coverage. Only room intents display the
new advice, during recording, below tracking/quality blockers. Native probes
check stationary views, viewpoint diversity, finite bounds and capacity.

## 5. Mobile delivery

New app downloads opt into lossless gzip wire compression. SHA-256 and the
64 MiB model limit bind the original decoded PLY, preserving all SH3 coefficients.
The existing verified raw transport remains available to older clients. Native
Foundation download acceptance, finite model import, capture transfer and
recording-lifecycle probes run before the Release IPA build. Source camera
metadata, full-screen navigation, rendering pixel limits and local export are
preserved. Actual iPhone memory, thermal and touch acceptance still require a
physical device; browser/CI checks cannot establish those results.


## Completed integration checks

App 0.1.9 build 12 was built from source commit
`e00a513d526cb1cb9cccefbc8fb916670d29841f`. The downloaded unsigned ARM64 IPA
passed ZIP integrity, version/build/source-commit checks, and exact bundled
asset comparison against that commit. Native coverage, Gaussian import,
full-capture transfer and Foundation gzip download probes passed before
Release compilation. Later PC-only fixes preserve the native API contract.

The exact candidate passed the current viewer's browser-generated simultaneous
touch movement/look, release-to-stop, reset, Walk/Orbit, portrait/landscape,
SH3 retention, pixel-budget and offline-resource checks. The diagnostic parity
hook was absent from the product page. These browser checks do not establish
physical iPhone performance.

The private PC revision is `gsplat-rgbd-detail-v2`. Complete older jobs upgrade
by retaining their original result and reusing the verified uploaded inventory.
Partial older uploads retain their chunks and advance revision only after the
complete inventory passes. The real same-input trial was attached to the job
with an explicit reused-compute receipt; the HTTP request did not secretly
train another model. Its authenticated TLS download matched the paired leaf
certificate and reproduced all 47,201,477 decoded bytes and the model digest;
lossless wire size was 44,726,807 bytes. Existing pairing credentials remain
local and unchanged.

Final delivery checks found older Windows bridge listeners sharing the port.
Only verified owned bridge processes were restarted after confirming no active
jobs. The launcher now uses exclusive address ownership, tested by rejecting
a second listener. A bounded 100-ms, 64-KiB drain fixes Windows resets when
rejecting small unauthenticated POST bodies; unauthenticated data is never
parsed or retained. Tests also cover an advertised body that never arrives.


Final host validation passed `compileall`, `git diff --check`, the public-file
credential/name/path scan, and the full suite: 435 passed, 140 skipped. Skipped
platform/dependency tests are not counted as passing. The real CUDA training,
actual-source pixel checks, candidate browser navigation and pinned-certificate
TLS model transfer provide separate execution evidence. Physical-device and
multi-room visual qualification remain open; they are not implied by this build.

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

A fresh same-input full-resolution candidate is running under
`runs/rgbd-room-193312-fullres-v1`. Visual comparison and its final result must
be recorded before declaring this candidate better than the original.

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

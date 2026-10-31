# Capture Splat iOS App

Capture Splat is a native iPhone capture app for recording Video 3DGS input. It writes `capture_splat.v0.3` export folders for the host pipeline in this repository.

## Quick room scan

The app opens in Room Scan with the Room Walkthrough intent already selected.
Tap the circular **Record** control, walk a slow connected path around the room
with overlapping views of corners and furniture, then tap **Finish**. Wait for
saving to finish; **Review your scan** opens automatically. Inspect the LiDAR
preview when available and choose **Export Scan**, or use **My Scans** later.
Capture records video, quality-gated RGB-D keyframes, and camera metadata;
generating the Gaussian splat still happens on the host computer.

The camera-first screen starts with a clear camera view, elapsed recording time,
recorded video-frame count, capture guidance, and the record control. Build
0.1.1 (2) is shown on the idle screen so it can be distinguished from old IPAs.
Guidance overlays and the map remain available in Capture details.
Coverage is capture guidance, not proof that the whole room is reconstructed.
Other capture intents, RoomPlan, sensor details, and diagnostics are under
**More options -> Capture details**; camera settings and optional World Studio
pairing are in the same menu. No subject lock, RoomPlan session, or pairing is required to record
a room. Frame-quality, thermal, and finalization checks still apply.

Cloud world generation and an in-app finished-splat viewer are not connected.
The review screen states that explicitly. The proposed first cloud trial and
the remaining World Studio backend work are described in
[Mobile cloud generation](../../../docs/mobile_cloud_generation.md).

## Build

Open `CaptureSplat.xcodeproj` in Xcode, select a physical iPhone, and run the
`CaptureSplat` scheme. For physical-device signing, copy
`CaptureSplat/Config/CaptureSplat.local.xcconfig.example` to
`CaptureSplat/Config/CaptureSplat.local.xcconfig`, then set a unique bundle
identifier and your development team. The local file is ignored by Git, so
device signing no longer dirties the shared Xcode project.

For Windows sideloading, run the **Build iPhone IPA** workflow in GitHub
Actions and download its `CaptureSplat-unsigned-IPA` artifact. The IPA is
unsigned; use a Windows sideloading tool to sign it with your Apple account
and install it on a connected iPhone.

The simulator cannot validate camera, LiDAR, motion, or real capture timing.
Use the [iPhone Xcode diagnostics](../../../docs/iphone_xcode_diagnostics.md)
playbook for crash triage and the two-cycle physical-device stability smoke.

## Export

The app writes session folders under the app Documents directory:

```text
capture.json
rgb/
depth/
confidence/
imu.csv
gps.csv
metadata/
room_plan/
geometry/
masks/person/
```

Stopping a capture waits for the continuous video and queued RGB-D writes before
publishing `capture.json`. `metadata/finalization_report.json` records whether
the bundle finalized cleanly, while `capture_policy.json` and
`sensor_capabilities.json` preserve the active quality policy and device
fallback evidence.

The **Projects** tab scans saved `capture_splat_*` folders on launch, foreground,
and after finalization. It lists finalized, partial, and malformed bundles
separately, opens an available LiDAR point preview, reports RoomPlan
availability, and shares the selected folder. Partial folders remain recovery
evidence; the app does not claim it can reconstruct missing in-memory frame
state or re-finalize them after relaunch.

On supported devices the quality-first capture configuration also samples
non-empty ARKit person stencils at up to 5 Hz and snapshots a capped classified
ARKit mesh during finalization. These derived sidecars are mask/geometry
proposals for host processing and review; they are not 3DGS, collision, metric,
semantic, or navigation authority. Unsupported devices keep recording the core
RGB-D stream and report the fallback in `sensor_capabilities.json` and
`session_events.jsonl`.

Video 3DGS Max is the default capture path. The app records dense, sharp RGB-D
keyframes with camera metadata and quality reports; the host pipeline handles
COLMAP and VkSplat training. Use the capture intent menu to tag the pass as
Desk / Cluster, Room Walkthrough, Object Orbit, Corridor / Passage, Wall /
Facade, Outdoor Object, RoomPlan + 3DGS, or Detail Repair without changing the
underlying quality gates.

Only Object Orbit uses a subject target. The user must tap **Lock Subject**
after the app reports stable center depth, and Record remains disabled until
that explicit lock succeeds. Record never locks a subject automatically. All
other capture intents remain full-scene passes. Video 3DGS Max always uses
Smart quality-gated keyframes; there is no timed capture fallback. Object Orbit
readiness requires low, middle, and high-angle support, and its per-frame mask
follows the projected target depth as the camera moves.

Supported LiDAR iPhones can open Room Plan review during the same video capture
workflow and export `room_plan/room.usdz`, `room_plan/room_plan_report.json`,
and `room_plan/room_semantics.json`. This is capture guidance and semantic
proposal evidence for layout coverage, not a 3DGS quality claim.

`metadata/spatial_guidance_report.json` records live-guidance coverage and
performance evidence. Version 0.2 distinguishes intentional anchor-update
throttling from actual drops, records processing-budget overruns, and reports
time spent with mesh, map-only, pose-only, or hidden guidance under each thermal
policy.

## World Studio pairing and dormant sender

Manual Export from Projects is the production iPhone workflow. Pairing and the
dormant sender are optional transport experiments; capture, reconstruction,
and World Studio package review never depend on them.

M1B-1 compiles an isolated bounded sender foundation into the app target. It
implements QR invitation parsing, P-256 device identity, Keychain-backed grants,
TLS 1.3 leaf-certificate pinning, signed requests with durable counters, a
checksummed frame/byte-bounded queue, ACK/resume reconciliation, limited
in-flight uploads, retry, paired desktop/device queue binding, and
thermal/storage/background/network pause policy.

The World Studio pairing sheet is now active only after an explicit user
action. It scans QR codes, resolves the exact `_capturesplat._tcp` service
identity from that invitation, pins TLS, waits for Mac approval, and recovers a
pending or current grant after restart without performing discovery at app
launch. Device keys, grants, pending signed requests, and an authoritative
one-Mac recovery pointer remain in Keychain. A checksummed rebuildable desktop
cache and durable request counters live under
`Application Support/CaptureSplat/live-sender/v0.1`.
The app exposes one current Mac at a time; revoke and forget it before pairing
another.

The frame sender remains deliberately dormant. `CaptureController.swift`,
keyframe acceptance, and atomic writers are unchanged; pairing does not open a
queue or upload a capture. The additive v0.2 contract derives a stable session
ID from an atomically persisted random seed before `capture.json` exists and
binds the final manifest reference only at finalization. The deterministic host
probe uses only immutable file references and never retains `ARFrame` or pixel
buffers. See [iOS Live Sender M1B-1](../../../docs/ios_live_sender.md) for the
ACK-index gate, integration boundary, and remaining physical gates.

Saved frames include `capture_quality` metadata. The host pipeline uses accepted
keyframes for ingest and COLMAP export, so rejected candidates remain diagnostic
evidence rather than trainer input.

# Semantic room audit

Research and read-only code audit. No capture mode, trained model, IPA, or PC
job was changed by this audit. Preserve the preferred original visual model as
the appearance baseline for any semantic integration.

## Existing useful functionality

- `CaptureController.swift` defaults to `room_walkthrough`. The existing
  `full_room_semantic` intent is named **RoomPlan + 3DGS**. Only that intent
  starts `RoomCaptureSession(arSession:)` alongside capture. It already has
  stop/finalization callbacks, RoomBuilder processing and USDZ/JSON export.
- In `ContentView.swift`, the selector is under **More options -> Capture
  details -> Capture Intent**. The minimal capture screen does not show the
  intent selector for an ordinary room. The feature is hidden in details and
  inactive under the default intent; it is not missing from the source.
- During recording, `ARCaptureView.swift` requests mesh classification when
  supported. The inspected existing capture has wall, floor, ceiling, door,
  window, seat and unknown mesh classes. Its intent is `room_walkthrough` and
  its RoomPlan directory is empty. Existing structural labels can therefore
  be investigated without claiming furniture instances were captured.
- `roomPlanSemanticsReport` exports walls, doors, windows, openings and object
  category/dimensions/transforms. It currently omits object attributes such as
  storage cabinet versus shelf, source identifiers, per-object confidence,
  relationships and the structured floor array. Indexed IDs are not stable
  source instance identifiers. The separate layout summary counts floors on
  supported OS versions, but that is not a full floor semantic record.
- Full-package transfer preserves the RoomPlan directory and referenced files.
  The private RGB-D trainer does not consume its object semantics and the
  bundled Gaussian viewer has no object-ID picking, label overlay or furniture
  grouping. Transferring a semantic file is not evidence of semantic use.
- `hybrid_surface.py` already implements a conservative classified-surface
  transfer lane for checksum-bound registered geometry. It does not provide
  generic cabinet/furniture recognition or automatically run in the private
  app viewer. Existing World Studio ownership boundaries still apply.

## Verified external capabilities

Apple RoomPlan produces parametric room structure and recognized furniture
categories. Its storage attributes distinguish cabinet and shelf. Objects
include geometric transforms and dimensions; these are estimates requiring
review. Default object geometry is simplified bounding geometry, not an exact
textured reconstruction of each piece of furniture.

Sources:
- https://developer.apple.com/augmented-reality/roomplan/
- https://machinelearning.apple.com/research/roomplan
- https://developer.apple.com/documentation/roomplan/storagetype
- https://developer.apple.com/documentation/roomplan/captured-object-attributes
- https://developer.apple.com/videos/play/wwdc2023/10192/

For existing scans and unsupported object categories, Grounded SAM 2 provides
pretrained text-grounded detection plus image/video segmentation. SAM 2 alone
produces masks rather than automatic category names. A detector such as
Grounding DINO supplies names. The resulting masks still require calibrated
RGB-D projection, visibility checks, instance association across views and
registration into the actual selected model frame. Neither repository turns
a Gaussian PLY into a universally correct semantic room by loading it alone.

- https://github.com/IDEA-Research/Grounded-SAM-2
- https://github.com/facebookresearch/sam2

The SAM 2 repository states Apache-2.0 licensing for model checkpoints and core
code. Grounded SAM 2 has separate component licences; pin and check the exact
code/checkpoint pair before integration. Its repository provides local model
paths as well as optional remote APIs; no paid API is necessary for the local
Grounding DINO/SAM 2 route. Actual peak memory and throughput on this PC are
unmeasured; do not promise an 8-GB real-time pipeline. A small SAM 2.1 model,
selected keyframes and sequential inference are evaluation candidates.

## Recommended implementation sequence

1. Enable the existing shared-session RoomPlan path for the room workflow while
   keeping local capture/finalization/manual export independent of its success.
   Validate camera-format stability, world tracking, two consecutive sessions,
   finalization latency and thermals on the physical iPhone.
2. Export additive semantic records with stable source instance IDs, category,
   supported attributes, confidence, dimensions, pose, floor polygons where
   available, units/frame declarations and source asset checksums. Retain
   uncertain labels as unknown and preserve older manifests.
3. Bind semantic geometry to the selected model frame. Shared camera/session
   history is useful evidence, not sufficient registration proof. Check actual
   corners, floor/wall boundaries and cabinet locations against captured data.
4. Add selectable semantic overlays to the existing original appearance model:
   wall/floor structure, object IDs and cabinet/furniture labels. Keep labelled
   proxy geometry distinct from measured detailed object geometry. Downstream
   World Studio owns world review, promotion and visualization integrations.
5. Evaluate pretrained Grounding DINO plus SAM 2 on existing frames for missing
   categories and furniture instances. Fuse confidence-filtered depth only
   where masks and visibility agree across views; do not invent unseen backs,
   fill unknown space as a wall or merge neighbouring cabinets indiscriminately.
6. Validate recognition separately from visual sharpness: class accuracy,
   instance duplication, per-object boundary alignment, source dimensions and
   missed/open doorways. Labels do not sharpen RGB textures or make geometry
   automatically solid, watertight, accurate or collision-safe.

The preferred architecture is original visual splat + registered semantic
structure + object instances. Clean planar geometry can represent reviewed
walls, and furniture instances can carry masks/meshes/proxies appropriate to
their evidence. RoomPlan is the practical first source already present in the
app; the pretrained detector/segmenter is the extension for broader categories
and old captures without RoomPlan output.

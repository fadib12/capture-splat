# One-record room capture

Room capture now defaults to the existing shared-session RoomPlan + 3DGS intent.
Ordinary room walkthroughs also request semantic capture. One Record starts RGB-D,
video and room recognition; Finish saves them as one package. No second RoomPlan
screen or additional scan is required for this path.

The AR view applies capture configuration before starting RoomPlan and then
leaves the shared session configuration under RoomPlan ownership. The recorder
waits briefly for that session's observed camera format before fixing writer
size; requested 4K is not presented as achieved resolution when RoomPlan chooses
a different format. Device-level format stability still requires validation.

Video finalization and RoomBuilder processing run in parallel. The existing
bounded semantic timeout and generation guards preserve local RGB-D export if
RoomPlan fails or returns late. Capture finalization completes only after both
lanes have finished or the semantic timeout has safely closed that lane.

Semantic JSON additions preserve source instance UUIDs, confidence, object
attributes (including cabinet/shelf storage types), floor records, units and
frame provenance. Standalone RoomPlan scans are explicitly `roomplan_local` and
cannot be silently attached to a separately recorded splat. Shared captures
identify their common ARKit session frame; geometry remains an estimate.

When a reconstruction PC is already paired, a finalized capture starts transfer
and reconstruction automatically, then opens the returned model. Pairing is
still a one-time prerequisite. Transfer errors leave the complete local scan
available; the PC progress view can be dismissed to use Projects/Manual Export.
A completed model does not interrupt an active new recording or finalization.

The worker reports source-manifest binding, model frame and units only for the
unnormalized ARKit-world result. The bridge validates those declarations against
the consumed training report. The phone verifies the returned source binding
against its upload inventory before attaching local semantic data to that model.
The viewer sidecar binds source, capture and model hashes. Native validation
limits JSON to 2 MiB / 512 entities and checks rigid transforms, finite bounds,
unique IDs and confidence values; mismatches retain the room without its overlay.

The offline viewer adds an optional Layout control with selectable walls,
floors, openings and furniture bounds. Labels use text nodes, confidence and
estimated dimensions. Cabinet/shelf attributes produce the corresponding labels.
Layout does not make the Gaussian model a watertight mesh, measured collider or
fully reconstructed editable furniture asset. RoomPlan's supported categories
are not every possible object. Grounded SAM is not installed by this change.

Native import probes exercise model-bound semantic data, cabinet attributes,
stable IDs, duplicate rejection, reflected transforms and unknown frames. The
existing full-capture and Foundation download probes remain required. Browser
fixtures must be labelled synthetic and never published as detections from an
old capture. Physical iPhone acceptance must check repeated captures, observed
camera format, tracking, semantic/splat alignment, finalization latency and
thermal behavior before any production-quality or all-room claim.

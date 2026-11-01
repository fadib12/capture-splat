# Recovering a video-only partial capture

`scripts/recover_partial_capture.py` creates a separate recovery package from a
partial capture whose video completed and whose contiguous camera index remains
available. The command never edits or promotes the original capture. Inputs and
outputs must be separate directories on the workspace data drive.

```powershell
python scripts/recover_partial_capture.py --source runs/recovery-inbox/SCAN --out runs/recovered-captures/SCAN
```

The tool bounds source scans and file sizes, rejects aliases, hashes the complete
source inventory, and validates each video-index ordinal, finite rigid OpenGL
camera pose, timestamp and calibrated intrinsics. It extracts up to 300 sharp
frames across the complete video at a maximum 3840-pixel edge, binds them to the
exact recorded video ordinals and excludes non-normal tracking. The source video
receipt, container frame count and complete camera index must agree.

Outputs include a separate `capture/` directory with a reconstructed v0.2
manifest, selected source-resolution RGB images, per-frame camera calibration,
portable Nerfstudio-style transforms and copied original assets. A separate
`colmap/` export contains camera priors and images; it has no triangulated tracks
or verified SfM registration. Source poses remain unchanged in the recovered
manifest; the COLMAP export applies the OpenCV camera-axis conversion once.

The ZIP contains the recovered capture package. The original failed-finalization
report remains unchanged inside that package. The separate recovery report says
what was recovered, excludes a native-finalization claim and remains `hold`.
Source SHA-256 evidence is revalidated after recovery. No missing per-frame depth
maps, camera poses, classes or native accepted keyframes are fabricated.

If no RGB-D keyframes were recorded, this is a video-only recovery with preserved
mesh/layout evidence, not input for the private measured-depth worker. It needs
an RGB-only reconstruction route. Any future model needs verified frame handling
before its preserved RoomPlan data can be overlaid. Recognition estimates and
camera priors do not establish final visual quality, metric or collision authority.

The observed partial capture retained a completed 2,790-frame video, full camera
index, RoomPlan and mesh, but zero accepted RGB-D frames. Recovery retained 299
normally tracked 3840x2160 images and preserved original video/layout/mesh bytes.
The app's missing-manifest error followed its nonempty-RGB-D finalization guard;
this recovery does not claim to have restored unrecorded depth or fixed the
on-device sensor configuration.

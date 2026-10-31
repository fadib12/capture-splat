# Realtor360 candidate reconstruction and repair pipeline

Checked 2026-10-31 against the official repositories. Decision: **hold**.
The sources are downloaded as external processes for inspection, not imported,
vendored into the Apache-2.0 application or represented as connected services.
No third-party demo received private room photographs.

| Stage | Official source pin | Evidence and next gate |
| --- | --- | --- |
| Capture | Capture Splat iOS | Existing images, calibrated intrinsics and ARKit/OpenGL camera-to-world poses; preserve immutable raw scans. |
| InfiniSplat v2 | `zju3dv/InfiniSplat` / `be49948e92f828054d98103cd98578fada8ec728` | Source and inference weights available. Project Registration License requires organizational app-development registration before use. No local registration receipt was found. No execution or weights download performed. |
| CleanGS | `smlab-niser/clean-gs` / `78a39e941c14df5857903b1112040d117f230ac4` | MIT. Accepts Gaussian PLY or gsplat checkpoint, calibrated COLMAP cameras and binary target masks. Masks and same-coordinate-frame cameras must exist before filtering. |
| GSFix3D | `GSFix3D/GSFix3D` / `6c8ac95c0eba448e4002461b7c96966d1acd4c04` | README explicitly restricts the entire project to non-commercial use because of Gaussian-Splatting components. Commercial permission is not recorded. Research environment and fine-tuning inputs are separate from the app runtime. |
| Viewing | Spark / Three | Existing offline app viewer accepts bounded Gaussian PLY and compact .splat; a successful load does not establish reconstruction quality. |

## What each stage actually does

InfiniSplat v2 uses pretrained multi-image reconstruction with optional supplied
camera poses. Its provided-camera NPZ has `c2w [N,4,4]` in OpenCV axes and
`intrinsics [N,3,3]` in original-image pixels, in exact image order. Our ARKit
poses must right-multiply `diag(1,-1,-1,1)` exactly once. Its exported `scene.ply`
has SH0 appearance; `cameras.npz` contains processed-image intrinsics and an
RDNorm reconstruction gauge. That bundle cannot be reused as original-image
calibration, nor described as validated metric geometry.

Default inference uses 32 views and a 518-pixel long edge; the CLI permits up to
50 views and a 2044-pixel long edge. Those settings do not prove 4K quality or
that they fit an RTX 3070. The authors recommend starting at 2-4 images and 224
pixels to test memory. Do not switch the app to this model before registration,
installation, pin/hash checks, actual target-GPU execution, bounded export and
same-input photographic comparison have passed.

CleanGS removes existing Gaussians using selected calibrated target masks and
visibility; it does not refine their geometry/appearance or synthesize missing
room regions. All-white room masks are not evidence of meaningful floater
cleanup. Defaults target contained objects and cannot be applied blindly to an
entire room. Its PLY loader distinguishes `gsplat` coefficient-major from `3dgs`
channel-major SH layouts; preserve the declared layout on both import and export.

GSFixer first repairs rendered novel-view images using diffusion. Its documented
own-data path needs captured RGB/rendered training pairs, scene fine-tuning,
selected novel cameras and rendered views, then a separate GSFix3D optimization
to lift repaired images into the Gaussian scene. Current scripts expect Replica
or ScanNet++-style inputs. It is not a one-command arbitrary-PLY enhancer. Filled
regions are generative hypotheses and must not be promoted as measured surfaces,
collision, navigation or physics authority.

## Integration gates

1. Retain finalized local captures and Manual Export independently of repair.
2. Record InfiniSplat project registration and applicable component terms; obtain
   GSFix3D commercial permission before using its restricted pipeline in a product.
3. Use separate pinned environments: current ReSplat is Torch 2.7/CUDA12.8;
   CleanGS documents Torch2.8/CUDA12.8; GSFix3D pins Torch2.4.1 and was tested on
   Ubuntu22.04/Python3.11/CUDA12.1/RTX4500Ada. Do not overwrite the working runtime.
4. Bind images, camera order, poses, dimensions, hashes, processing transforms,
   model and render cameras. In normalized/exported gauges, validate full camera
   rotations as well as centers; never feed original ARKit cameras to an unrelated
   Gaussian coordinate frame.
5. Evaluate the same held-out source cameras before/after each stage, inspect
   supported interior views and retain the raw model beside every derived model.
   Record mask provenance, removed count, detail loss and generated regions.
6. Promote only the named visual claim after evidence; keep metric/collision and
   navigation authority separate. A 4K display setting cannot recover missing RGB
   detail or unobserved surfaces.

## Registration

The project licence requires company/entity name, contact name and valid email,
project name and intended use. A complete accurate submission grants permission
without a fee or a separate approval response. Public source access does not
prove that this particular project was registered. No confirmation was found in
local project documentation; private form submissions cannot be checked publicly.

Suggested intended-use text for the owner to review:

> Realtor360/Capture Splat is an iOS room-capture application. We intend to use
> pretrained InfiniSplat v2 on a backend to reconstruct Gaussian scenes from
> captured room images and calibrated camera poses, then review and display the
> resulting scenes in the app.

The organization and contact must be supplied accurately by the project owner.

Sources: [InfiniSplat v2 guide](https://github.com/zju3dv/InfiniSplat/tree/main/InfiniSplat_v2),
[Project Registration License](https://github.com/zju3dv/InfiniSplat/blob/main/LICENSE),
[registration form](https://docs.google.com/forms/d/e/1FAIpQLSff5QjjANlJ2hnXYqBSAzKWxEM58gIxPLh17-t3j3-MqO1a5w/viewform),
[CleanGS](https://github.com/smlab-niser/clean-gs),
[GSFix3D usage and licence](https://github.com/GSFix3D/GSFix3D#license).

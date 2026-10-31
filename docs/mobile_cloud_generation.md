# Mobile scan-to-world cloud proposal

Status: proposal, not a deployed cloud service. The iPhone records and finalizes a
capture locally. Version 0.1.4 adds local trained-splat import and an offline viewer;
see [Local Brush training](local_brush_training.md). Upload, cloud jobs,
completed-world delivery and notifications remain unimplemented.
Source changes require a fresh macOS build and installation of its resulting IPA;
repacking an older compiled binary does not apply interface changes.

The intended product flow is Record -> Finish -> Review -> Create World ->
Processing -> Open World. A capture preview is not a generated Gaussian world.
The review screen currently states that cloud generation is not connected;
it does not display simulated jobs or completion states.

## Provider and first trial

Use Runpod as the proposed GPU host for a supervised first room reconstruction.
Start with one Secure Cloud NVIDIA A40 or RTX A6000 Pod with 48 GB VRAM, subject
to availability. This is a trial configuration, not a measured capacity,
quality, or speed claim. CPU RAM, storage, CUDA, compiler, and SfM dependencies
also need validation. Recheck the console quote before renting anything.

At the time this proposal was checked, the public pricing page listed both
cards at $0.59 per GPU hour. Storage and other billable services are separate;
there is no measured per-room price. Provider rates can change. A proposed
initial test budget of $10 is not an enforced billing cap or authorization to
spend. Do not provision a Pod, paid storage, or automatic top-ups until the
account owner approves the actual quote and trial duration.

1. Create a Runpod account at <https://console.runpod.io/>. Account registration
   alone does not deploy the app. Keep provider credentials out of chat,
   source files, IPA bundles, and Git.
2. Build and validate the reconstruction environment before starting a paid
   experiment. Follow [Cloud GPU Setup](cloud_gpu_setup.md), including CUDA
   compatibility when using gsplat. Keep external trainers pinned and
   user-installed; do not vendor GPL implementations.
3. Agree on the exact Pod quote, storage size, region, and maximum trial
   duration. Upload only a deliberately selected finalized capture for this
   trial, retaining the original on the phone.
4. Run capture validation, preparation, SfM, training, finite-output checks,
   and fixed-camera visual review on that same capture. Record hardware,
   commands, dependency pins, logs, actual billed runtime, and artifacts.
   A held result is not a finished-world quality promotion.
5. Retrieve the results and end the compute trial. Verify separately whether
   retained volumes or storage are still billable. Do not describe a stopped
   Pod as free storage.

After this succeeds, evaluate a queue-based Serverless worker for the
production generation path. A proposed starting configuration is zero active
workers, at most one concurrent worker, and an explicit execution timeout
chosen from measured reconstruction duration. Those are proposed settings,
not a cost cap. The default timeout may be too short for reconstruction.
An image and pipeline must pass a real end-to-end trial before deployment.

Provider references:

- [GPU pricing](https://www.runpod.io/pricing)
- [Choosing a Pod](https://docs.runpod.io/pods/choose-a-pod)
- [Serverless endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)

## Backend ownership and work still required

Capture Splat owns capture, preparation, SfM/trainer adaptation, and evidence.
World Studio owns generation jobs, review, promotion, Spark visualization, and
published world results. Do not add an independent job authority to the capture
app or reuse the experimental LAN sender as a cloud uploader. The current
[World Studio README](https://github.com/sandeep-devarapalli/world-studio/blob/main/README.md)
does not advertise a hosted SaaS service or a production worker by default.

The following work remains in the backend and its mobile client:

- An authenticated World Studio HTTPS API with per-user access controls,
  private capture storage, bounded resumable uploads, exact capture checksums,
  cancellation, retention, and deletion behavior. The phone must not contain
  the Runpod administrator API key. A user must explicitly request upload of
  a particular saved scan.
- A durable job contract and provider adapter with idempotent job creation,
  bounded resource use, retry and cancellation, observable failures, and
  restart recovery. Reopening the iPhone app must recover actual server state.
- A worker running the evidence-gated pipeline against the immutable selected
  package, publishing result checksums and provenance. Job completion must
  remain separate from visual quality promotion and metric/collision authority.
- A World Studio mobile viewer route that loads the exact authorized result,
  with expiry-aware access, mobile memory checks, and orbit/reset controls.
  Walk mode requires its separate supported-volume and navigation boundaries;
  an attractive splat alone does not establish safe traversal.
- An iPhone client with verified upload progress, actual job status, failure
  recovery, and an Open World action only after an authorized real result
  exists. Optional notifications must not be necessary for job completion.

No provider API, endpoint, user account, storage bucket, or job is created by
this proposal. Account access and a reviewed deployment configuration are
required before cloud setup can be completed. Manual export remains available
throughout.

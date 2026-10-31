# ReSplat on the local PC

Status: input adapter and experimental TLS PC bridge/mobile client implemented.
The runtime and direct physical iPhone flow still require acceptance. A source
change requires the new 0.1.5 (6) IPA; older IPAs cannot submit scans.

The existing pretrained implementation is external `cvg/resplat`, pinned to
`cae7ddc4cdbd80e05e9f5fa00f5ea02c4e9056b1`. Code and published model card list
MIT licensing. Keep dependencies, weights and results out of Git. The small
checkpoint is downloaded and SHA-256 verified:
`548993fede0d9536d2d914cbe51e0ebea0ad6f88c898c909e02127d59bb2be9a`.

## Windows prerequisite

After the user enabled virtualization, the dedicated Ubuntu installation
succeeded. WSL sees the RTX 3070. Python 3.12 and the isolated CUDA 12.8 runtime
are installed by `scripts/setup_resplat_wsl.sh`; completing installation alone
does not validate model inference or memory capacity.

Enable Intel VMX virtualization in BIOS, and enable Virtual Machine Platform
in Windows Features if needed. Restart Windows after saving work. Then retry:

```powershell
wsl --install Ubuntu-24.04 --name CaptureSplat --location "$PWD/external/wsl-resplat" --no-launch
wsl -d CaptureSplat -u root -- nvidia-smi
```

After Linux and GPU access work, install the upstream documented isolated
Python 3.12 / PyTorch 2.7.0 / CUDA 12.8 environment, gsplat 1.5.3 and pointops.
Do not overwrite the host pipeline's `.venv`. CUDA compilation and runtime
memory still require validation; the RTX 3070's 8 GB is not a proven capacity
for this model. No paid GPU service is required for this selected PC trial.

## Prepared capture contract

```powershell
.venv/Scripts/python.exe scripts/prepare_resplat_capture.py --capture captures/my_scan --out runs/my_scan_resplat
```

This creates checksum-bound RGB images and a COLMAP text camera model. It uses
accepted RGB-D images and ARKit pose priors; it does not run SfM. Camera axes
convert to OpenCV exactly once through the existing COLMAP exporter. Sensor
depth is not applied as a training loss. Input files stay untouched.

The initial small preset selects eight context views. It is a runtime smoke
candidate, not evidence of full-room coverage or improved detail. The upstream
demo rotates its PLY into a context-view frame; never treat the resulting file
as ARKit-world or meter-validated geometry. Finite PLY and viewer load alone do
not promote quality. Keep same-input Brush results as a control, then compare
fixed-camera renders against matching source images.

## Experimental direct iPhone path

Install the host certificate dependency with `pip install -e '.[pc]'`. After
the GPU environment is installed and tested, start this single-operator service
from the repository root, substituting the PC's private Wi-Fi/Ethernet address:

```powershell
.venv/Scripts/python.exe scripts/start_resplat_pc.py --host 192.168.1.10
```

The service generates a local TLS certificate and a random bearer token in
ignored `runs/pc-bridge/`. Its connection code contains both the certificate
fingerprint and token: treat it like a password. Never publish it. The app
stores it in this device's Keychain and rejects redirects or mismatched TLS
certificates. No SpAItial key is used or sent to the phone.

In the newly compiled app choose More -> Connect reconstruction PC, paste the
code from `runs/pc-bridge/connection-code.txt`, and connect. After a finalized
scan choose Create room splat -> Create splat on PC. This explicitly sends only
accepted RGB photographs and the capture manifest; video, raw depth and mesh
are not uploaded by this trial. Keep the app open. Reopening and submitting the
same unchanged scan resumes stored image uploads or observes its existing run.
Failed runs require PC inspection; they are never silently rerun or promoted.

One GPU process runs at a time. The bridge bounds itself to eight scans, 512
images per manifest, 25 MiB per image, 512 MiB uploaded per scan, and 64 MiB
mobile results. Archive the service's old scans explicitly when it reaches its
limit. Do not move the source scans. The PC must stay awake; restart the service
manually after a PC restart. Existing Windows firewall rules may require a
private-network exception for TCP 8770. Do not expose it to the public internet.

Job completion returns a checksum-bound `.splat`; it grants no quality or
metric authority. The small pretrained model selects eight context views, so
this trial must not be sold as complete-room reconstruction. This single-user
experimental executor is not a replacement for production World Studio jobs,
per-user permissions, reviewed publication or cloud retention/deletion.

The intended flow is a saved scan explicitly submitted to an authenticated PC
service, reconstruction, then a verified model downloaded into the existing
viewer. Local finalization and Manual Export must remain usable. This is a
post-capture transfer, separate from optional experimental live streaming.
Backend jobs and publication remain the World Studio boundary. Do not expose
an unauthenticated file server or invent processing progress while the GPU
runtime is unavailable. An app change requires a freshly compiled IPA.

References: [ReSplat](https://github.com/cvg/resplat),
[WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands),
[ASUS Intel VMX setup](https://www.asus.com/global/support/faq/1043786/).

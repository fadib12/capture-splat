# Bundled room viewer

The offline viewer uses [Spark](https://github.com/sparkjsdev/spark), version
**2.4.0**, npm source commit **ffe210dc0a45d0515a06c9235878108112827d61**.
Spark is MIT licensed; its complete license is in `LICENSE`.
Its npm integrity is
`sha512-aUjgBfhwDRcF3nUwiAD6Nh7IwvxEmW9liz2qrAXIm2BlPYBmn81QbXIpGFLBSr/o0TKGvEXbNIsKujgPGV/7QQ==`.

[Three.js](https://github.com/mrdoob/three.js), version **0.180.0**, supplies
the WebGL integration and unmodified `OrbitControls`. Its MIT license is in
`THREE-LICENSE`. Spark's unmodified `PointerControls` supplies touch look,
pinch movement and two-finger pan. Capture Splat adds the visible movement pad,
horizontal movement, height controls, speed selection, reset and rendering presets
in `main.source.js`. Dependency implementations are bundled, not rewritten.

`main.js` includes Three, Spark, the decoder WASM and its blob worker. There are
**no CDN requests or downloaded runtime dependencies**. The native scheme handler
serves only `index.html`, `main.js` and the selected `model.ply` or `model.splat`.
Source/build/license files are not exposed through the scheme handler.
The CSP permits WebAssembly compilation and local blob workers, without enabling
general JavaScript eval or remote connections. Native metadata injection contains
only validated constants, never source strings or filesystem paths.

Full binary Gaussian `.ply` preserves SH0–SH3 appearance coefficients. The compact
`.splat` fallback contains only DC color, opacity and Gaussian geometry. Smooth
mode lowers drawing-buffer resolution; it does not discard model coefficients.
Both imports are bounded at 64 MiB. The PLY contract accepts only float properties,
known Gaussian fields, one vertex element (at most 2 million vertices), exact byte
length, finite values, usable rotations and bounded log scales. Mesh PLY is rejected.
Decoded CPU/GPU memory is larger than the file; a 61 MB file still needs physical
iPhone memory/thermal acceptance. A successful load alone is not visual-quality,
metric-scale, collision or navigation-safety evidence.

Detail mode uses device pixels up to a 3840-pixel long edge, an 8,294,400-pixel budget
and the GPU's texture limit. Smooth reduces that to 1920 / 2,073,600 pixels. These
are raster limits, not a claim that a capture has 4K detail. Rendering pauses when
the viewer is hidden, and unchanged views stop drawing after pending worker updates.
Moving in Walk mode does not implement collision detection.

Desktop exports with the identity CV context-camera marker use +X right, +Y down,
+Z forward. The viewer maps this to Three's camera axes once, by rotating the
camera around X by pi. When the sidecar supplies a finite `gravity_up: [x,y,z]`
in model CV coordinates, native code normalizes and serializes those numbers, and
the viewer uses that vector for horizon, horizontal movement and orbit. Otherwise,
model -Y is the fallback up vector. Legacy imports without the marker keep the
previous OpenGL (+Y up, -Z forward) starting convention.

## Rebuild

From the repository root, install the pinned build inputs into the ignored external
directory, then regenerate the checked-in `main.js`:

```powershell
npm.cmd install --prefix external/viewer-build --no-audit --no-fund --ignore-scripts --save-exact @sparkjsdev/spark@2.4.0 three@0.180.0 esbuild@0.25.12
node apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer/build-viewer.mjs
node --check apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer/main.js
```

Build tooling is not executed by the iPhone app. The complete folder is already an
Xcode resource folder reference; no additional runtime asset route is necessary.

Sensor-aligned RGB-D jobs additionally supply a bounded OpenGL camera rotation and
position in the preserved ARKit world frame. Native code validates a proper
orthonormal rotation and finite position, injects numeric values only, and the
viewer applies that camera without a CV flip. Legacy context-CV imports retain
their existing one-flip path. Both use the same movement controls.

Viewer source: https://github.com/antimatter15/splat
Pinned commit: ba182b51b7c2ad5738cdd6741cd63336d27470fb
License: MIT (included as LICENSE).

Modifications: remove sample cameras and remote default model; load only the selected
local model, disable automatic orbit, use a minimal touch UI, and report unsupported
WebGL2. Renderer and worker remain the upstream implementation. The compact .splat
format preserves Gaussian shape, opacity and DC color, not higher-order SH color.

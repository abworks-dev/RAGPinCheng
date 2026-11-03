# Local attachment viewers

Runtime packages are pinned in package-lock.json. They are lazy loaded only when opening a DXF/IFC preview. No cloud viewer, external font, model asset, or conversion service is used.

- dxf-viewer 1.0.50 — MPL-2.0, https://github.com/vagran/dxf-viewer. Unmodified package source is bundled into the application/worker; corresponding covered source is the locked npm package (including LICENSE). Preserve MPL notices and make that source available when distributing compiled artifacts. Transitive earcut 3.x ISC, loglevel 1.x MIT, opentype.js 1.3.x MIT (see locked package notices).
- web-ifc 0.0.78 — MPL-2.0, https://github.com/ThatOpen/engine_web-ifc. Unmodified JS and WASM come from the locked npm package. Vite emits the imported web-ifc.wasm as a local hashed asset; no hand-downloaded binary. Preserve the package LICENSE.md and supply corresponding MPL-covered source on distribution.
- three 0.186.0 — MIT, https://github.com/mrdoob/three.js. Includes OrbitControls; preserve the MIT copyright/license notice from node_modules/three/LICENSE.

Build/deployment switch: VITE_ATTACHMENT_MODEL_PREVIEW=false disables both native model viewers but retains metadata, download, and associated image/PDF previews. Rebuild frontend after changing the switch. SKP, DWG, RVT, CHE, NWD/NWC, MAX are explicitly download/associated-preview only; no native support is implied.

Bounds: original preview download <=32 MiB, timeout30s; image header <=512KiB and <=80 million pixels/single side32768; IFC <=500000 triangles/5000 geometry parts. Workers terminate on close/error/timeout. Limits are a lightweight viewer envelope, not upload limits. Original file downloads retain the existing authorized download path.

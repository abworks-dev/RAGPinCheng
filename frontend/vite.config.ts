import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

const viewerNotices = {
  name: "local-viewer-license-notices",
  generateBundle(this: { emitFile: (asset: { type: "asset"; fileName: string; source: string }) => void }) {
    const notices = [
      ["dxf-viewer", "LICENSE"], ["web-ifc", "LICENSE.md"], ["three", "LICENSE"],
      ["earcut", "LICENSE"], ["loglevel", "LICENSE-MIT"], ["opentype.js", "LICENSE"],
    ].map(([name, license]) => `## ${name}\n${readFileSync(new URL(`./node_modules/${name}/${license}`, import.meta.url), "utf8")}`).join("\n\n");
    this.emitFile({ type: "asset", fileName: "third-party-viewers.txt", source: readFileSync(new URL("./THIRD_PARTY_VIEWERS.md", import.meta.url), "utf8") + "\n\n" + notices });
  },
};

export default defineConfig({
  plugins: [react(), viewerNotices],
  server: {
    port: 5173,
    // Atomic editor staging directories are not application source.
    watch: { ignored: ["**/*.tmpdir/**"] },
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000",
        changeOrigin: false,
        // SSE needs flowing connections — disable Vite's response buffering.
        ws: false,
      },
    },
  },
});

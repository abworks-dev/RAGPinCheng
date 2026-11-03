import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { PREVIEW_TIMEOUT_MS } from "../lib/attachment-formats";

export default function AttachmentModel({ blob, kind }: { blob: Blob; kind: "dxf" | "ifc" }) {
  const container = useRef<HTMLDivElement>(null);
  const fit = useRef<() => void>(() => {});
  const [status, setStatus] = useState("正在加载本地预览引擎…");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let teardown = () => {};
    let disposed = false;
    const dispose = () => { if (!disposed) { disposed = true; teardown(); } };
    let worker: Worker | undefined;
    let observer: ResizeObserver | undefined;
    const url = URL.createObjectURL(blob);
    const fail = (message: string) => { if (active) { setError(message); setStatus(""); active = false; dispose(); worker?.terminate(); } };
    const timer = window.setTimeout(() => fail("预览超过 30 秒限制，请下载后查看"), PREVIEW_TIMEOUT_MS);
    setError(""); setStatus("正在解析模型…");
    void (async () => {
      if (kind === "dxf") {
        const { DxfViewer } = await import("dxf-viewer");
        if (!active || !container.current) return;
        const viewer = new DxfViewer(container.current, { autoResize: true, antialias: false, retainParsedDxf: false });
        teardown = () => viewer.Destroy();
        await viewer.Load({ url, fonts: [], workerFactory: () => {
          worker = new Worker(new URL("../lib/dxf-preview.worker.ts", import.meta.url), { type: "module" }); return worker;
        } });
        if (!active) return;
        fit.current = () => { const bounds = viewer.GetBounds(); if (bounds) viewer.FitView(bounds.minX, bounds.maxX, bounds.minY, bounds.maxY); };
        fit.current();
        setStatus("DXF 二维预览 · 滚轮缩放，拖动平移；不加载外部字体或参照");
        window.clearTimeout(timer);
      } else {
        const THREE = await import("three");
        const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");
        if (!active || !container.current) return;
        const root = container.current;
        const renderer = new THREE.WebGLRenderer({ antialias: false });
        renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
        root.appendChild(renderer.domElement);
        const scene = new THREE.Scene(); scene.background = new THREE.Color(0xf1f5f9);
        const camera = new THREE.PerspectiveCamera(45, 1, .01, 1e7);
        const controls = new OrbitControls(camera, renderer.domElement);
        scene.add(new THREE.HemisphereLight(0xffffff, 0x64748b, 3));
        const light = new THREE.DirectionalLight(0xffffff, 2); light.position.set(1, 3, 2); scene.add(light);
        const group = new THREE.Group(); scene.add(group);
        const render = () => { if (active) renderer.render(scene, camera); };
        controls.addEventListener("change", render);
        const resize = () => { renderer.setSize(root.clientWidth, root.clientHeight); camera.aspect = root.clientWidth / Math.max(1, root.clientHeight); camera.updateProjectionMatrix(); render(); };
        observer = new ResizeObserver(resize); observer.observe(root); resize();
        teardown = () => { observer?.disconnect(); controls.dispose(); group.traverse((object) => { if (object instanceof THREE.Mesh) { object.geometry.dispose(); const materials = Array.isArray(object.material) ? object.material : [object.material]; materials.forEach((material) => material.dispose()); } }); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove(); };
        worker = new Worker(new URL("../lib/ifc-preview.worker.ts", import.meta.url), { type: "module" });
        worker.onerror = () => fail("IFC 解析引擎出错，请下载后查看");
        worker.onmessage = (event: MessageEvent<{ error?: string; meshes: { positions: Float32Array; indices: Uint32Array; transform: number[]; color: number[] }[] }>) => {
          if (!active) return;
          if (event.data.error) { fail(event.data.error); return; }
          try {
            for (const part of event.data.meshes) {
              const geometry = new THREE.BufferGeometry(); geometry.setAttribute("position", new THREE.BufferAttribute(part.positions, 3)); geometry.setIndex(new THREE.BufferAttribute(part.indices, 1)); geometry.computeVertexNormals();
              const material = new THREE.MeshStandardMaterial({ color: new THREE.Color(part.color[0], part.color[1], part.color[2]), side: THREE.DoubleSide, opacity: part.color[3], transparent: part.color[3] < 1 });
              const mesh = new THREE.Mesh(geometry, material); mesh.applyMatrix4(new THREE.Matrix4().fromArray(part.transform)); group.add(mesh);
            }
            fit.current = () => { const box = new THREE.Box3().setFromObject(group); const center = box.getCenter(new THREE.Vector3()); const radius = Math.max(box.getSize(new THREE.Vector3()).length(), 1); camera.position.copy(center).add(new THREE.Vector3(radius, radius, radius)); camera.near = radius / 10000; camera.far = radius * 100; camera.updateProjectionMatrix(); controls.target.copy(center); controls.update(); render(); };
            fit.current(); setStatus("IFC 三维预览 · 拖动旋转，滚轮缩放，双指平移"); window.clearTimeout(timer); worker?.terminate();
          } catch { fail("模型渲染失败，请下载后查看"); }
        };
        const buffer = await blob.arrayBuffer(); if (active) worker.postMessage(buffer, [buffer]);
      }
    })().catch((error) => fail(error instanceof Error ? error.message : "预览引擎不可用"));
    return () => { active = false; window.clearTimeout(timer); worker?.terminate(); observer?.disconnect(); dispose(); URL.revokeObjectURL(url); };
  }, [blob, kind]);
  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex flex-wrap items-center gap-2 border-b border-border p-2"><Button size="sm" variant="outline" onClick={() => fit.current()} disabled={!status.includes("预览 ·")}>适合窗口</Button><p role={error ? "alert" : "status"} className="min-w-0 flex-1 break-words text-xs">{error || status}</p></div>
    <div ref={container} className="relative min-h-0 flex-1 overflow-hidden" aria-label={`${kind.toUpperCase()} 模型画布`} />
  </div>;
}

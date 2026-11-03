import { IfcAPI } from "web-ifc";
import wasmUrl from "web-ifc/web-ifc.wasm?url";

self.onmessage = async (event: MessageEvent<ArrayBuffer>) => {
  const api = new IfcAPI();
  let model: number | undefined;
  try {
    // Vite emits the pinned WASM locally. No runtime CDN or referenced model files.
    await api.Init(() => wasmUrl);
    model = api.OpenModel(new Uint8Array(event.data), { COORDINATE_TO_ORIGIN: true });
    if (model < 0) throw new Error("IFC 文件无法解析");
    const meshes: { positions: Float32Array; indices: Uint32Array; transform: number[]; color: number[] }[] = [];
    let triangles = 0;
    api.StreamAllMeshes(model, (mesh) => {
      for (let i = 0; i < mesh.geometries.size(); i++) {
        const placed = mesh.geometries.get(i);
        const geometry = api.GetGeometry(model!, placed.geometryExpressID);
        try {
          const vertices = api.GetVertexArray(geometry.GetVertexData(), geometry.GetVertexDataSize());
          const indices = api.GetIndexArray(geometry.GetIndexData(), geometry.GetIndexDataSize()).slice();
          triangles += indices.length / 3;
          if (triangles > 500_000 || meshes.length > 5_000) throw new Error("模型超过轻量预览限制（50 万三角面 / 5000 构件），请下载后查看");
          const positions = new Float32Array(vertices.length / 2);
          for (let j = 0; j < vertices.length / 6; j++) positions.set(vertices.subarray(j * 6, j * 6 + 3), j * 3);
          meshes.push({ positions, indices, transform: placed.flatTransformation, color: [placed.color.x, placed.color.y, placed.color.z, placed.color.w] });
        } finally { geometry.delete(); }
      }
    });
    if (!meshes.length) throw new Error("IFC 中没有可显示的几何体");
    self.postMessage({ meshes }, { transfer: meshes.flatMap((mesh) => [mesh.positions.buffer, mesh.indices.buffer]) });
  } catch (error) { self.postMessage({ error: error instanceof Error ? error.message : "IFC 预览失败" }); }
  finally { if (model !== undefined && model >= 0) api.CloseModel(model); api.Dispose(); }
};

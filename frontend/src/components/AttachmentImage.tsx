import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { imageFitScale } from "../lib/attachment-formats";

export function AttachmentImage({ url, title }: { url: string; title: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [box, setBox] = useState({ width: 1, height: 1 });
  const [rotation, setRotation] = useState(0);
  const [zoom, setZoom] = useState<number | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setBox({ width: entry.contentRect.width, height: entry.contentRect.height }));
    if (viewport.current) observer.observe(viewport.current);
    return () => observer.disconnect();
  }, []);
  const scale = zoom ?? imageFitScale(size.width || 1, size.height || 1, box.width, box.height, rotation);
  const swapped = Math.abs(rotation % 180) === 90;
  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex flex-wrap items-center gap-2 border-b border-border p-2">
      <Button variant="outline" size="sm" aria-label="缩小图片" onClick={() => setZoom(Math.max(.05, scale / 1.25))}>−</Button>
      <span className="text-xs tabular-nums" aria-live="polite">{Math.round(scale * 100)}%</span>
      <Button variant="outline" size="sm" aria-label="放大图片" onClick={() => setZoom(Math.min(8, scale * 1.25))}>＋</Button>
      <Button variant="outline" size="sm" onClick={() => setZoom(null)}>适合窗口</Button>
      <Button variant="outline" size="sm" onClick={() => setZoom(1)}>实际大小</Button>
      <Button variant="outline" size="sm" onClick={() => { setRotation((v) => v + 90); setZoom(null); }}>旋转</Button>
      <Button variant="outline" size="sm" onClick={() => { void viewport.current?.requestFullscreen?.().catch(() => setError("浏览器未允许全屏，可继续在窗口查看")); }}>全屏</Button>
      <span className="text-xs text-muted-foreground">{size.width ? `${size.width} × ${size.height} 像素` : "读取图片尺寸…"}</span>
    </div>
    {error && <p role="alert" className="p-2 text-sm text-destructive">{error}</p>}
    <div ref={viewport} className="min-h-0 flex-1 overflow-auto bg-muted p-4" aria-label="图片画布" tabIndex={0}>
      <div className="relative m-auto" style={{ width: (swapped ? size.height : size.width) * scale, height: (swapped ? size.width : size.height) * scale }}>
        <img alt={title} src={url} draggable={false} onError={() => setError("图片无法解码，请下载后查看")} onLoad={(event) => {
          const image = event.currentTarget;
          if (image.naturalWidth * image.naturalHeight > 80_000_000) { setError("图片超过 8000 万像素预览限制，请下载后查看"); return; }
          setSize({ width: image.naturalWidth, height: image.naturalHeight });
        }} style={{ position: "absolute", maxWidth: "none", width: size.width * scale || undefined, height: size.height * scale || undefined, left: "50%", top: "50%", transform: `translate(-50%, -50%) rotate(${rotation}deg)` }} />
      </div>
    </div>
    <div className="flex items-center gap-2 border-t border-border p-2"><img src={url} alt="当前图片缩略图" className="h-10 w-14 rounded border border-border object-contain" /><span className="truncate text-xs">{title}</span></div>
  </div>;
}

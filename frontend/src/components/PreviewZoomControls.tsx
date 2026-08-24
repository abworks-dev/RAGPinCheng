import { Minus, Plus, RotateCcw } from "lucide-react";
import { Button } from "./ui/button";
export const PREVIEW_ZOOM_MIN = 0.5;
export const PREVIEW_ZOOM_MAX = 2;
export const PREVIEW_ZOOM_STEP = 0.1;
export function PreviewZoomControls({ zoom, onChange }: { zoom: number; onChange: (next: number) => void }) {
  const update = (delta: number) => onChange(Math.min(PREVIEW_ZOOM_MAX, Math.max(PREVIEW_ZOOM_MIN, Number((zoom + delta).toFixed(2)))));
  return <div className="flex items-center gap-1" aria-label="预览缩放"><Button size="icon" variant="ghost" aria-label="缩小" title="缩小" disabled={zoom <= PREVIEW_ZOOM_MIN} onClick={() => update(-PREVIEW_ZOOM_STEP)}><Minus className="size-4" /></Button><span className="w-12 text-center text-xs tabular-nums text-muted-foreground">{Math.round(zoom * 100)}%</span><Button size="icon" variant="ghost" aria-label="放大" title="放大" disabled={zoom >= PREVIEW_ZOOM_MAX} onClick={() => update(PREVIEW_ZOOM_STEP)}><Plus className="size-4" /></Button><Button size="icon" variant="ghost" aria-label="重置缩放" title="重置缩放" disabled={zoom === 1} onClick={() => onChange(1)}><RotateCcw className="size-4" /></Button></div>;
}

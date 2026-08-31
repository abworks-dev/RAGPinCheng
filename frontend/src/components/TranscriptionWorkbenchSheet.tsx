import { useState } from "react";
import { Badge } from "./ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";
import { TranscriptionVersionPanel } from "./TranscriptionVersionPanel";

export function TranscriptionWorkbenchSheet({
  open,
  title,
  originalFilename,
  mediaId,
  schemeName,
  schemeDeleted,
  refreshToken,
  initialAction,
  initialVersionId,
  onClose,
  onChanged,
}: {
  open: boolean;
  title: string;
  originalFilename: string;
  mediaId: string | null;
  schemeName?: string | null;
  schemeDeleted?: boolean;
  refreshToken?: string | null;
  initialAction?: "edit-current" | null;
  initialVersionId?: string | null;
  onClose: () => void;
  onChanged?: () => void | Promise<void>;
}) {
  const [dirty, setDirty] = useState(false);
  const requestClose = () => {
    if (dirty && !window.confirm("当前修改尚未保存，确定关闭转写工作台吗？")) return;
    onClose();
  };
  return (
    <Sheet open={open} onOpenChange={(nextOpen) => { if (!nextOpen) requestClose(); }}>
      <SheetContent
        closeLabel="关闭转写工作台"
        className="gap-0 p-0 md:w-[min(75rem,90vw)] md:max-w-none"
      >
        <SheetHeader className="space-y-1 border-b border-border px-4 py-4 pr-16 sm:px-6 sm:py-5">
          <SheetTitle className="truncate text-ui-lg">{title || "转写工作台"}</SheetTitle>
          <SheetDescription className="truncate" title={originalFilename}>
            {originalFilename || "查看版本、审核转录并管理发布状态"}
          </SheetDescription>
          {(schemeName || schemeDeleted) && (
            <div className="flex flex-wrap items-center gap-1.5 text-ui-xs text-muted-foreground" data-testid="workbench-scheme-line">
              <span>转录方案：{schemeName ? <span className="font-medium text-foreground">{schemeName}</span> : "原转录配置已删除"}</span>
              {schemeName && schemeDeleted && <Badge variant="secondary">原转录配置已删除</Badge>}
            </div>
          )}
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
          {mediaId && (
            <TranscriptionVersionPanel
              mediaId={mediaId}
              refreshToken={refreshToken}
              initialAction={initialAction}
              initialVersionId={initialVersionId}
              embedded
              onChanged={onChanged}
              onDirtyChange={setDirty}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

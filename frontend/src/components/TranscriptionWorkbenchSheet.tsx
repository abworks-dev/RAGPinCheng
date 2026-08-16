import { useState } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";
import { TranscriptionVersionPanel } from "./TranscriptionVersionPanel";

export function TranscriptionWorkbenchSheet({
  open,
  title,
  originalFilename,
  mediaId,
  refreshToken,
  onClose,
  onChanged,
}: {
  open: boolean;
  title: string;
  originalFilename: string;
  mediaId: string | null;
  refreshToken?: string | null;
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
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
          {mediaId && (
            <TranscriptionVersionPanel
              mediaId={mediaId}
              refreshToken={refreshToken}
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

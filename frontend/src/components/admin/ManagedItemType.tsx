import { Captions, FileCode2, FileSpreadsheet, FileText, FileType2, Folder, Presentation } from "lucide-react";

export function ManagedItemType({ docType, folder = false }: { docType?: string | null; folder?: boolean }) {
  if (folder) {
    return <div className="flex w-20 flex-col items-center gap-1 text-center text-ui-xs font-medium text-muted-foreground"><Folder className="size-6 text-primary" aria-hidden="true" /><span>文件夹</span></div>;
  }

  const definition = ({
    pdf: ["PDF", FileText, "text-destructive"],
    docx: ["Word", FileType2, "text-primary"],
    xlsx: ["Excel", FileSpreadsheet, "text-success"],
    pptx: ["PPT", Presentation, "text-warning"],
    markdown: ["Markdown", FileCode2, "text-foreground"],
    transcript: ["视频转录稿", Captions, "text-primary"],
  } as const)[docType || ""] || (["其他", FileText, "text-muted-foreground"] as const);
  const [label, Icon, color] = definition;

  return <div className="flex w-20 flex-col items-center gap-1 text-center text-ui-xs font-medium" title={label}><Icon className={`size-6 ${color}`} aria-hidden="true" /><span className="max-w-full break-words leading-tight">{label}</span></div>;
}

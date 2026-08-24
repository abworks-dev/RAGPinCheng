import { Captions, FileCode2, FileSpreadsheet, FileText, FileType2, Film, Folder, FolderSync, Network, Presentation } from "lucide-react";

export function ManagedItemType({ docType, folder = false, sharedFolder = false, compact = false }: { docType?: string | null; folder?: boolean; sharedFolder?: boolean; compact?: boolean }) {
  const widthClass = compact ? "w-16" : "w-20";
  if (folder) {
    const Icon = sharedFolder ? FolderSync : Folder;
    const label = sharedFolder ? "共享文件夹" : "文件夹";
    return <div className={`flex ${widthClass} flex-col items-center gap-1 text-center text-ui-xs font-medium text-muted-foreground`} title={label}><Icon className={`size-6 ${sharedFolder ? "text-info" : "text-primary"}`} aria-hidden="true" /><span>{label}</span></div>;
  }

  const normalizedType = (docType || "").toLowerCase().replace(/^\./, "");
  const canonicalType = ({ doc: "docx", docx: "docx", xls: "xlsx", xlsx: "xlsx", ppt: "pptx", pptx: "pptx", md: "markdown", markdown: "markdown", pdf: "pdf", xmind: "xmind", transcript: "transcript", video: "video" } as Record<string, string>)[normalizedType] || normalizedType;
  const definition = ({
    pdf: ["PDF", FileText, "text-destructive"],
    docx: ["Word", FileType2, "text-primary"],
    xlsx: ["Excel", FileSpreadsheet, "text-success"],
    pptx: ["PPT", Presentation, "text-warning"],
    xmind: ["XMind", Network, "text-primary"],
    markdown: ["Markdown", FileCode2, "text-foreground"],
    transcript: ["视频转录稿", Captions, "text-primary"],
    video: ["视频", Film, "text-primary"],
  } as Record<string, readonly [string, typeof FileText, string]>)[canonicalType] || (["其他", FileText, "text-muted-foreground"] as const);
  const [label, Icon, color] = definition;

  return <div className={`flex ${widthClass} flex-col items-center gap-1 text-center text-ui-xs font-medium`} title={label}><Icon className={`size-6 ${color}`} aria-hidden="true" /><span className="max-w-full break-words leading-tight">{label}</span></div>;
}

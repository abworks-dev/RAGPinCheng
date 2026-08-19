const SUPPORTED_UPLOAD_EXTENSIONS = new Set([".pdf", ".md", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"]);

export interface FolderUploadEntry {
  file: File;
  relativePath: string;
}

export interface FolderUploadSelection {
  entries: FolderUploadEntry[];
  ignoredEntries: FolderUploadEntry[];
  rootFolderNames: string[];
  folderCount: number;
  fileCount: number;
  scannedFileCount: number;
  totalSize: number;
}

export type DroppedUploadSelection =
  | { mode: "files"; files: File[] }
  | { mode: "folder"; selection: FolderUploadSelection };

interface FileSystemEntryLike {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
}

interface FileSystemFileEntryLike extends FileSystemEntryLike {
  file: (success: (file: File) => void, failure?: (error: DOMException) => void) => void;
}

interface FileSystemDirectoryReaderLike {
  readEntries: (
    success: (entries: FileSystemEntryLike[]) => void,
    failure?: (error: DOMException) => void,
  ) => void;
}

interface FileSystemDirectoryEntryLike extends FileSystemEntryLike {
  createReader: () => FileSystemDirectoryReaderLike;
}

type DirectoryDataTransferItem = DataTransferItem & {
  webkitGetAsEntry?: () => FileSystemEntryLike | null;
};

function normalizeRelativePath(path: string, fallbackName: string): string {
  const candidate = (path || fallbackName).replaceAll("\\", "/");
  const parts = candidate.split("/");
  if (
    candidate.length > 1024
    || candidate.includes("\0")
    || candidate.startsWith("/")
    || /^[A-Za-z]:/.test(candidate)
    || parts.some((part) => !part || part === "." || part === "..")
  ) {
    throw new Error("文件夹包含无效的文件路径");
  }
  return parts.join("/");
}

export function isSupportedUploadFile(file: File): boolean {
  const dot = file.name.lastIndexOf(".");
  return dot >= 0 && SUPPORTED_UPLOAD_EXTENSIONS.has(file.name.slice(dot).toLowerCase());
}

export function summarizeFolderEntries(entries: FolderUploadEntry[]): FolderUploadSelection {
  const normalized = entries.map(({ file, relativePath }) => ({
    file,
    relativePath: normalizeRelativePath(relativePath, file.name),
  }));
  const accepted = normalized.filter(({ file }) => isSupportedUploadFile(file));
  const ignored = normalized.filter(({ file }) => !isSupportedUploadFile(file));
  const folders = new Set<string>();
  const roots = new Set<string>();

  accepted.forEach(({ relativePath }) => {
    const parts = relativePath.split("/");
    if (parts.length > 1) roots.add(parts[0]);
    for (let index = 1; index < parts.length; index += 1) {
      folders.add(parts.slice(0, index).join("/"));
    }
  });

  return {
    entries: accepted,
    ignoredEntries: ignored,
    rootFolderNames: Array.from(roots),
    folderCount: folders.size,
    fileCount: accepted.length,
    scannedFileCount: normalized.length,
    totalSize: accepted.reduce((total, entry) => total + entry.file.size, 0),
  };
}

export function folderSelectionFromFiles(files: File[]): FolderUploadSelection {
  return summarizeFolderEntries(files.map((file) => ({
    file,
    relativePath: file.webkitRelativePath || file.name,
  })));
}

function readFileEntry(entry: FileSystemFileEntryLike): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function readDirectoryBatch(reader: FileSystemDirectoryReaderLike): Promise<FileSystemEntryLike[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function readDirectoryEntries(entry: FileSystemDirectoryEntryLike): Promise<FileSystemEntryLike[]> {
  const reader = entry.createReader();
  const entries: FileSystemEntryLike[] = [];
  while (true) {
    const batch = await readDirectoryBatch(reader);
    if (!batch.length) return entries;
    entries.push(...batch);
  }
}

async function traverseEntry(entry: FileSystemEntryLike, parentPath: string): Promise<FolderUploadEntry[]> {
  const relativePath = normalizeRelativePath(
    parentPath ? `${parentPath}/${entry.name}` : entry.name,
    entry.name,
  );
  if (entry.isFile) {
    const file = await readFileEntry(entry as FileSystemFileEntryLike);
    return [{ file, relativePath }];
  }
  if (!entry.isDirectory) return [];
  const children = await readDirectoryEntries(entry as FileSystemDirectoryEntryLike);
  const nested = await Promise.all(children.map((child) => traverseEntry(child, relativePath)));
  return nested.flat();
}

export async function collectDroppedUpload(dataTransfer: DataTransfer): Promise<DroppedUploadSelection> {
  const items = Array.from(dataTransfer.items || []);
  const entries = items.map((item) => (item as DirectoryDataTransferItem).webkitGetAsEntry?.() || null);
  const hasDirectory = entries.some((entry) => entry?.isDirectory);
  if (!hasDirectory) {
    return { mode: "files", files: Array.from(dataTransfer.files || []) };
  }

  const traversed: FolderUploadEntry[] = [];
  for (let index = 0; index < items.length; index += 1) {
    const entry = entries[index];
    if (entry) {
      traversed.push(...await traverseEntry(entry, ""));
      continue;
    }
    const file = items[index].getAsFile();
    if (file) traversed.push({ file, relativePath: file.name });
  }
  return { mode: "folder", selection: summarizeFolderEntries(traversed) };
}

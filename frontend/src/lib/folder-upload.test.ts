import { describe, expect, it } from "vitest";
import {
  collectDroppedUpload,
  folderSelectionFromFiles,
  summarizeFolderEntries,
} from "./folder-upload";

function fileWithPath(name: string, relativePath: string, contents = "content") {
  const file = new File([contents], name);
  Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
  return file;
}

describe("folder upload helpers", () => {
  it("summarizes supported files, non-empty folders, ignored files and size", () => {
    const guide = fileWithPath("guide.md", "资料包/01 建筑/guide.md", "guide");
    const report = fileWithPath("report.pdf", "资料包/report.pdf", "report");
    const video = fileWithPath("demo.mp4", "资料包/视频/demo.mp4", "video");

    const selection = folderSelectionFromFiles([guide, report, video]);

    expect(selection.entries.map((entry) => entry.relativePath)).toEqual([
      "资料包/01 建筑/guide.md",
      "资料包/report.pdf",
    ]);
    expect(selection.ignoredEntries.map((entry) => entry.relativePath)).toEqual([
      "资料包/视频/demo.mp4",
    ]);
    expect(selection.rootFolderNames).toEqual(["资料包"]);
    expect(selection.folderCount).toBe(2);
    expect(selection.fileCount).toBe(2);
    expect(selection.scannedFileCount).toBe(3);
    expect(selection.totalSize).toBe(11);
  });

  it("rejects traversal and absolute relative paths", () => {
    const file = new File(["x"], "guide.md");
    expect(() => summarizeFolderEntries([{ file, relativePath: "../guide.md" }])).toThrow("文件夹包含无效的文件路径");
    expect(() => summarizeFolderEntries([{ file, relativePath: "C:/guide.md" }])).toThrow("文件夹包含无效的文件路径");
  });

  it("reads every directory-reader batch recursively and skips empty folders", async () => {
    const guide = new File(["guide"], "guide.md");
    const note = new File(["note"], "note.txt");
    const fileEntry = (file: File) => ({
      isFile: true,
      isDirectory: false,
      name: file.name,
      file: (success: (value: File) => void) => success(file),
    });
    const directoryEntry = (name: string, batches: unknown[][]) => ({
      isFile: false,
      isDirectory: true,
      name,
      createReader: () => {
        let index = 0;
        return {
          readEntries: (success: (entries: unknown[]) => void) => success(batches[index++] || []),
        };
      },
    });
    const root = directoryEntry("资料包", [
      [fileEntry(guide)],
      [directoryEntry("其他", [[fileEntry(note)], []])],
      [],
    ]);
    const dataTransfer = {
      files: [] as unknown as FileList,
      items: [{ webkitGetAsEntry: () => root, getAsFile: () => null }] as unknown as DataTransferItemList,
    } as DataTransfer;

    const dropped = await collectDroppedUpload(dataTransfer);

    expect(dropped.mode).toBe("folder");
    if (dropped.mode === "folder") {
      expect(dropped.selection.entries[0].relativePath).toBe("资料包/guide.md");
      expect(dropped.selection.ignoredEntries[0].relativePath).toBe("资料包/其他/note.txt");
      expect(dropped.selection.folderCount).toBe(1);
    }
  });

  it("keeps a plain file drop in ordinary file mode", async () => {
    const file = new File(["guide"], "guide.md");
    const dataTransfer = {
      files: [file] as unknown as FileList,
      items: [{ webkitGetAsEntry: () => null, getAsFile: () => file }] as unknown as DataTransferItemList,
    } as DataTransfer;

    await expect(collectDroppedUpload(dataTransfer)).resolves.toEqual({ mode: "files", files: [file] });
  });
});

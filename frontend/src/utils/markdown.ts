/**
 * 用于预览文本的 Markdown 清理工具
 * 移除 Markdown 语法标记，保留纯文本内容
 */

/**
 * 清理 Markdown 语法，返回纯文本
 * 用于参考来源预览和 Tooltip 显示
 */
export function stripMarkdown(text: string): string {
  if (!text) return "";

  return (
    text
      // 1. 移除代码块标记
      .replace(/^```[\s\S]*?```$/gm, "")
      // 2. 移除行内代码标记
      .replace(/`([^`]+)`/g, "$1")
      // 3. 移除标题标记 (#, ##, ###, ####, #####, ######)
      .replace(/^#{1,6}\s+/gm, "")
      // 4. 移除粗体和斜体标记 (**, *, __, _)
      .replace(/\*\*([^*]+?)\*\*/g, "$1")
      .replace(/\*([^*]+?)\*/g, "$1")
      .replace(/__([^_]+?)__/g, "$1")
      .replace(/\b_([^_]+?)_\b/g, "$1")
      // 5. 移除删除线标记
      .replace(/~~([^~]+?)~~/g, "$1")
      // 6. 移除链接标记，只保留文字 [text](url) → text
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      // 7. 移除图片标记
      .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
      // 8. 移除列表标记（有序和无序列表）
      .replace(/^[-*+]\s+/gm, "")
      .replace(/^\d+\.\s+/gm, "")
      // 9. 移除引用标记
      .replace(/^>\s*/gm, "")
      // 10. 移除水平分割线
      .replace(/^[-*_]{3,}$/gm, "")
      // 11. 处理表格：移除管道标记，保留内容
      .replace(/^\|/gm, "")
      .replace(/\|$/gm, "")
      .replace(/\|/g, " ")
      // 12. 移除表格分隔行 (|---|---|)
      .replace(/^[-:\s|]+$/gm, "")
      // 13. 清理多余的空白行（连续多行空行合并为一行）
      .replace(/\n\s*\n\s*\n/g, "\n\n")
      // 14. 移除行首行尾的空白
      .trim()
  );
}

/**
 * 清理 Markdown 并截断到指定长度，用于预览
 */
export function getMarkdownPreview(text: string, maxChars: number = 400): string {
  const clean = stripMarkdown(text);
  if (clean.length <= maxChars) return clean;
  return clean.slice(0, maxChars) + "…";
}

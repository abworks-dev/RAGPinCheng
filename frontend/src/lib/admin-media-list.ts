import type { MediaAsset, TranscriptionJob } from "../types";

/**
 * Sorting and search helpers for the transcription task list.
 *
 * The list is rendered as a responsive card grid instead of a table, so the
 * sortable columns are declared here and reused both by the desktop column
 * headers and by the compact sort control shown on narrow viewports.
 */
export type MediaListSortKey = "media" | "progress" | "submitted";
export type SortDirection = "asc" | "desc";
export type MediaListSort = { key: MediaListSortKey; direction: SortDirection };

export const MEDIA_LIST_SORT_LABELS: Record<MediaListSortKey, string> = {
  media: "媒体信息",
  progress: "处理进度",
  submitted: "最近提交",
};

export const MEDIA_LIST_SORT_KEYS: MediaListSortKey[] = ["media", "progress", "submitted"];

/** Newest submission first, matching the order the server returns the list in. */
export const DEFAULT_MEDIA_LIST_SORT: MediaListSort = { key: "submitted", direction: "desc" };

export const SORT_DIRECTION_LABELS: Record<SortDirection, string> = {
  asc: "升序",
  desc: "降序",
};

/** Clicking a new column starts ascending; clicking the active column flips it. */
export function nextMediaListSort(current: MediaListSort, key: MediaListSortKey): MediaListSort {
  if (current.key !== key) return { key, direction: "asc" };
  return { key, direction: current.direction === "asc" ? "desc" : "asc" };
}

const TERMINAL_PROGRESS_RANK = 6;

/**
 * Lifecycle rank used by the "处理进度" column: ascending means "less far along
 * the pipeline", so both directions stay meaningful. Failed and cancelled
 * entries are terminal and therefore sort after the finished ones.
 */
export function mediaProgressRank(asset: MediaAsset, job?: TranscriptionJob): number {
  const failed =
    job?.status === "failed"
    || asset.status === "failed"
    || asset.publication_status === "publication_failed"
    || asset.publication_index_status === "failed"
    || asset.publication_request_status === "failed";
  if (failed || job?.status === "cancelled") return TERMINAL_PROGRESS_RANK;
  if (
    asset.current_phase === "review"
    || asset.review_status === "awaiting_review"
    || asset.status === "transcript_ready"
  ) {
    return 2;
  }
  if (
    asset.current_phase === "ready"
    || asset.publication_status === "published"
    || asset.status === "ready"
  ) {
    return 5;
  }
  if (
    asset.current_phase === "index"
    || asset.current_phase === "publication"
    || asset.publication_status === "publishing"
    || asset.publication_request_status === "publishing"
    || (asset.publication_index_status != null && asset.publication_index_status !== "done")
  ) {
    return 3;
  }
  if (job?.status === "running" || asset.status === "transcribing" || asset.current_phase === "transcription") return 1;
  if (job?.status === "succeeded") return 5;
  return 0;
}

function progressRatio(job?: TranscriptionJob): number {
  if (!job) return 0;
  if (job.status === "succeeded") return 1;
  if (job.total_ms != null && job.total_ms > 0) {
    return Math.min(1, Math.max(0, job.processed_ms / job.total_ms));
  }
  return 0;
}

function compareText(left: string, right: string): number {
  return left.localeCompare(right, "zh-CN", { numeric: true, sensitivity: "base" });
}

export type MediaListSortContext = {
  /** Latest transcription job of an asset, when one exists. */
  jobFor: (asset: MediaAsset) => TranscriptionJob | undefined;
};

/** Deterministic, stable ordering: every key falls back to title, then media id. */
export function compareMediaAssets(
  left: MediaAsset,
  right: MediaAsset,
  sort: MediaListSort,
  context: MediaListSortContext,
): number {
  const leftJob = context.jobFor(left);
  const rightJob = context.jobFor(right);
  let comparison = 0;
  if (sort.key === "media") {
    comparison = compareText(left.title, right.title) || compareText(left.original_filename, right.original_filename);
  } else if (sort.key === "submitted") {
    comparison = left.created_at - right.created_at;
  } else {
    comparison =
      mediaProgressRank(left, leftJob) - mediaProgressRank(right, rightJob)
      || progressRatio(leftJob) - progressRatio(rightJob);
  }
  if (comparison !== 0) return sort.direction === "asc" ? comparison : -comparison;
  const tiebreak = compareText(left.title, right.title) || compareText(left.media_id, right.media_id);
  return sort.direction === "asc" ? tiebreak : -tiebreak;
}

export function sortMediaAssets(
  assets: MediaAsset[],
  sort: MediaListSort,
  context: MediaListSortContext,
): MediaAsset[] {
  return [...assets].sort((left, right) => compareMediaAssets(left, right, sort, context));
}

export type MediaSearchInput = {
  asset: MediaAsset;
  job?: TranscriptionJob;
  categoryPath?: string | null;
  /** Product labels of the current statuses (job, media, review, publication). */
  statusLabels?: (string | null | undefined)[];
  /** Human readable origin, e.g. 人工转写 / 共享目录视频. */
  extraLabels?: (string | null | undefined)[];
};

/**
 * Searchable text of one task row. Only operator-visible concepts are indexed:
 * title, file name, archive folder, status wording and the like.
 */
export function mediaSearchHaystack({
  asset,
  job,
  categoryPath,
  statusLabels = [],
  extraLabels = [],
}: MediaSearchInput): string {
  return [
    asset.title,
    asset.original_filename,
    categoryPath ?? asset.category_path ?? "",
    job?.status,
    asset.status,
    asset.review_status,
    asset.publication_status,
    asset.publication_index_status,
    asset.publication_request_status,
    asset.storage_kind === "external" ? "共享目录" : "",
    asset.transcript_origin === "manual" ? "人工转写" : "",
    ...statusLabels,
    ...extraLabels,
  ]
    .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
}

/**
 * Every whitespace separated term must be present, so "楼梯 失败" narrows the
 * list instead of widening it.
 */
export function matchesMediaSearch(query: string, haystack: string): boolean {
  const terms = query.trim().toLocaleLowerCase("zh-CN").split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  return terms.every((term) => haystack.includes(term));
}

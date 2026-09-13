import { describe, expect, it } from "vitest";
import type { MediaAsset, TranscriptionJob } from "../types";
import {
  DEFAULT_MEDIA_LIST_SORT,
  DEFAULT_MEDIA_PANEL_FILTERS,
  MEDIA_CATEGORY_NONE_VALUE,
  MEDIA_SCHEME_DELETED_VALUE,
  countMediaPanelFilters,
  matchesMediaPanelFilters,
  matchesMediaSearch,
  mediaProgressRank,
  mediaSchemeOf,
  mediaSearchHaystack,
  mediaSubmittedBucket,
  nextMediaListSort,
  sortMediaAssets,
  type MediaListSort,
} from "./admin-media-list";

function asset(overrides: Partial<MediaAsset> = {}): MediaAsset {
  return {
    media_id: "media-1",
    title: "项目交付培训",
    original_filename: "training.mp4",
    mime_type: "video/mp4",
    file_size: 1024,
    transcript_origin: null,
    status: "uploaded",
    created_at: 1_700_000_000,
    updated_at: 1_700_000_000,
    error: null,
    available_actions: [],
    disabled_actions: {},
    ...overrides,
  };
}

function job(overrides: Partial<TranscriptionJob> = {}): TranscriptionJob {
  return {
    job_id: "job-1",
    media_id: "media-1",
    attempt_number: 1,
    profile_id: "profile-1",
    status: "pending",
    stage: null,
    processed_ms: 0,
    total_ms: null,
    failure_error_code: null,
    error_summary: null,
    failure: null,
    result_version_id: null,
    created_at: 1_700_000_000,
    started_at: null,
    finished_at: null,
    updated_at: 1_700_000_000,
    ...overrides,
  };
}

function context(jobs: Record<string, TranscriptionJob | undefined> = {}) {
  return { jobFor: (item: MediaAsset) => jobs[item.media_id] };
}

const titles = (assets: MediaAsset[]) => assets.map((item) => item.title);

describe("nextMediaListSort", () => {
  it("starts ascending when a different column is clicked", () => {
    expect(nextMediaListSort(DEFAULT_MEDIA_LIST_SORT, "media")).toEqual({ key: "media", direction: "asc" });
  });

  it("flips the direction when the active column is clicked again", () => {
    const ascending: MediaListSort = { key: "media", direction: "asc" };
    expect(nextMediaListSort(ascending, "media")).toEqual({ key: "media", direction: "desc" });
    expect(nextMediaListSort({ key: "media", direction: "desc" }, "media")).toEqual({ key: "media", direction: "asc" });
  });
});

describe("sortMediaAssets", () => {
  const assets = [
    asset({ media_id: "b", title: "楼梯专项检查", created_at: 300 }),
    asset({ media_id: "a", title: "Archicad 入门", created_at: 100 }),
    asset({ media_id: "c", title: "桥梁施工", created_at: 200 }),
  ];

  it("sorts by media information in both directions (zh-CN collation)", () => {
    // zh-CN locale collation places Han characters before Latin ones.
    expect(titles(sortMediaAssets(assets, { key: "media", direction: "asc" }, context()))).toEqual([
      "楼梯专项检查",
      "桥梁施工",
      "Archicad 入门",
    ]);
    expect(titles(sortMediaAssets(assets, { key: "media", direction: "desc" }, context()))).toEqual([
      "Archicad 入门",
      "桥梁施工",
      "楼梯专项检查",
    ]);
  });

  it("sorts by submission time in both directions", () => {
    expect(titles(sortMediaAssets(assets, { key: "submitted", direction: "asc" }, context()))).toEqual([
      "Archicad 入门",
      "桥梁施工",
      "楼梯专项检查",
    ]);
    expect(titles(sortMediaAssets(assets, { key: "submitted", direction: "desc" }, context()))).toEqual([
      "楼梯专项检查",
      "桥梁施工",
      "Archicad 入门",
    ]);
  });

  it("sorts by lifecycle progress and keeps failed work at the end", () => {
    const items = [
      asset({ media_id: "review", title: "待审核", review_status: "awaiting_review", current_phase: "review" }),
      asset({ media_id: "failed", title: "失败任务", status: "failed", current_phase: "failed" }),
      asset({ media_id: "running", title: "转录中", status: "transcribing", current_phase: "transcription" }),
      asset({ media_id: "ready", title: "已完成", status: "ready", publication_status: "published", current_phase: "ready" }),
    ];
    const jobs = { running: job({ media_id: "running", status: "running", processed_ms: 0, total_ms: null }) };

    expect(titles(sortMediaAssets(items, { key: "progress", direction: "asc" }, context(jobs)))).toEqual([
      "转录中",
      "待审核",
      "已完成",
      "失败任务",
    ]);
    expect(titles(sortMediaAssets(items, { key: "progress", direction: "desc" }, context(jobs)))).toEqual([
      "失败任务",
      "已完成",
      "待审核",
      "转录中",
    ]);
  });

  it("ranks a failed job above a cancelled one and keeps ties stable", () => {
    expect(mediaProgressRank(asset({ status: "failed" }))).toBe(6);
    expect(mediaProgressRank(asset(), job({ status: "cancelled" }))).toBe(6);
    expect(mediaProgressRank(asset({ status: "ready" }))).toBe(5);
    const sameTitle = [
      asset({ media_id: "b", title: "同名", created_at: 5 }),
      asset({ media_id: "a", title: "同名", created_at: 5 }),
    ];
    expect(sortMediaAssets(sameTitle, { key: "submitted", direction: "asc" }, context()).map((item) => item.media_id)).toEqual([
      "a",
      "b",
    ]);
  });

  it("does not mutate the input list", () => {
    const items = [asset({ media_id: "b", title: "乙" }), asset({ media_id: "a", title: "甲" })];
    const snapshot = titles(items);
    sortMediaAssets(items, { key: "media", direction: "asc" }, context());
    expect(titles(items)).toEqual(snapshot);
  });
});

describe("matching the search box", () => {
  it("indexes title, file name, folder and status wording", () => {
    const haystack = mediaSearchHaystack({
      asset: asset({
        title: "楼梯专项检查",
        original_filename: "2.4.10-楼梯.mp4",
        category_path: "公司标准 / 培训视频",
        status: "failed",
        review_status: "awaiting_review",
        publication_status: "not_published",
      }),
      job: job({ status: "failed" }),
      categoryPath: "公司标准 / 培训视频",
      statusLabels: ["转录失败", "待人工审核", "未发布"],
    });
    expect(matchesMediaSearch("楼梯", haystack)).toBe(true);
    expect(matchesMediaSearch("2.4.10", haystack)).toBe(true);
    expect(matchesMediaSearch("培训视频", haystack)).toBe(true);
    expect(matchesMediaSearch("转录失败", haystack)).toBe(true);
    expect(matchesMediaSearch("待人工审核", haystack)).toBe(true);
    expect(matchesMediaSearch("已发布", haystack)).toBe(false);
  });

  it("requires every term of a multi-word query", () => {
    const haystack = mediaSearchHaystack({ asset: asset({ title: "楼梯专项检查", status: "failed" }), statusLabels: ["失败"] });
    expect(matchesMediaSearch("楼梯 失败", haystack)).toBe(true);
    expect(matchesMediaSearch("楼梯 已发布", haystack)).toBe(false);
    expect(matchesMediaSearch("   ", haystack)).toBe(true);
  });

  it("is case insensitive for latin file names", () => {
    const haystack = mediaSearchHaystack({ asset: asset({ original_filename: "BIM-Training.MP4" }) });
    expect(matchesMediaSearch("bim-training", haystack)).toBe(true);
  });
});

describe("filter panel behind the search box", () => {
  const nowSec = Date.UTC(2026, 8, 13, 4, 0, 0) / 1000; // 2026-09-13T04:00Z
  const matches = (item: MediaAsset, filters = DEFAULT_MEDIA_PANEL_FILTERS, job?: TranscriptionJob) =>
    matchesMediaPanelFilters({ asset: item, job, filters, nowSec });

  it("counts only non-default filters, including the status shortcut", () => {
    expect(countMediaPanelFilters(DEFAULT_MEDIA_PANEL_FILTERS, false)).toBe(0);
    expect(countMediaPanelFilters(DEFAULT_MEDIA_PANEL_FILTERS, true)).toBe(1);
    expect(
      countMediaPanelFilters({ scheme: "scheme-1", category: "cat-1", submitted: "week" }, true),
    ).toBe(4);
  });

  it("filters by transcription scheme, including the deleted marker", () => {
    const assigned = asset({ media_id: "a", transcription_scheme_id: "scheme-1", transcription_scheme_name: "受控中文转录" });
    const deleted = asset({ media_id: "b", transcription_scheme_id: "scheme-old", transcription_scheme_deleted: true });
    const none = asset({ media_id: "c" });

    expect(matches(assigned, { scheme: "scheme-1", category: "", submitted: "" })).toBe(true);
    expect(matches(deleted, { scheme: "scheme-1", category: "", submitted: "" })).toBe(false);
    expect(matches(deleted, { scheme: MEDIA_SCHEME_DELETED_VALUE, category: "", submitted: "" })).toBe(true);
    expect(matches(none, { scheme: MEDIA_SCHEME_DELETED_VALUE, category: "", submitted: "" })).toBe(false);
    expect(mediaSchemeOf(assigned)).toEqual({ schemeId: "scheme-1", name: "受控中文转录", deleted: false });
    // The latest job wins over the asset snapshot.
    expect(mediaSchemeOf(assigned, job({ scheme_id: "scheme-2", scheme_name: "  工程转录  ", scheme_deleted: true }))).toEqual({
      schemeId: "scheme-2",
      name: "工程转录",
      deleted: true,
    });
  });

  it("filters by archive folder, including rows without one", () => {
    const filed = asset({ media_id: "a", category_id: "cat-05" });
    const unfiled = asset({ media_id: "b", category_id: null });

    expect(matches(filed, { scheme: "", category: "cat-05", submitted: "" })).toBe(true);
    expect(matches(unfiled, { scheme: "", category: "cat-05", submitted: "" })).toBe(false);
    expect(matches(unfiled, { scheme: "", category: MEDIA_CATEGORY_NONE_VALUE, submitted: "" })).toBe(true);
    expect(matches(filed, { scheme: "", category: MEDIA_CATEGORY_NONE_VALUE, submitted: "" })).toBe(false);
  });

  it("buckets submission time by local day and rolling windows", () => {
    const today = nowSec - 3600;
    const sixDaysAgo = nowSec - 6 * 24 * 3600;
    const twentyDaysAgo = nowSec - 20 * 24 * 3600;
    const longAgo = nowSec - 90 * 24 * 3600;

    expect(mediaSubmittedBucket(today, nowSec)).toBe("today");
    expect(mediaSubmittedBucket(sixDaysAgo, nowSec)).toBe("week");
    expect(mediaSubmittedBucket(twentyDaysAgo, nowSec)).toBe("month");
    expect(mediaSubmittedBucket(longAgo, nowSec)).toBe("older");
    expect(mediaSubmittedBucket(0, nowSec)).toBe("older");

    expect(matches(asset({ created_at: today }), { scheme: "", category: "", submitted: "today" })).toBe(true);
    expect(matches(asset({ created_at: longAgo }), { scheme: "", category: "", submitted: "today" })).toBe(false);
  });

  it("combines every panel filter with AND semantics", () => {
    const item = asset({ category_id: "cat-05", created_at: nowSec - 3600, transcription_scheme_id: "scheme-1" });

    expect(matches(item, { scheme: "scheme-1", category: "cat-05", submitted: "today" })).toBe(true);
    expect(matches(item, { scheme: "scheme-1", category: "cat-09", submitted: "today" })).toBe(false);
    expect(matches(item)).toBe(true);
  });
});

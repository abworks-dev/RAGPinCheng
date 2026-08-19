import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  api,
  setContentPermissionForbiddenHandler,
  setCsrfToken,
  setUnauthorizedHandler,
} from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  setCsrfToken(null);
  setUnauthorizedHandler(null);
  setContentPermissionForbiddenHandler(null);
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("sends cookies, JSON and CSRF for a mutating request", async () => {
    setCsrfToken("csrf-123");
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ id: 1, employee_id: "u1", real_name: "测试", role: "user", csrf_token: "next" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.login("u1", "password");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ employee_id: "u1", password: "password" }),
        headers: {
          "content-type": "application/json",
          "X-CSRF-Token": "csrf-123",
        },
      }),
    );
  });

  it("preserves GET requests without a CSRF header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ users: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.adminListUsers();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
  });

  it("loads the admin production system overview as a credentialed GET", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ topology: "unknown", checked_at: 1, app: {}, gpu: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await api.adminSystemOverview();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/system-overview",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
  });

  it("sends the managed-content delete handle with CSRF protection", async () => {
    setCsrfToken("csrf-delete");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      item_id: "item-1",
      version_id: "version-1",
      archived_at: 2,
      previous_status: "published",
      publication_withdrawn: true,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.deleteManagedContent("item/1", "version-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/content/items/item%2F1",
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        body: JSON.stringify({ expected_version_id: "version-1" }),
        headers: {
          "content-type": "application/json",
          "X-CSRF-Token": "csrf-delete",
        },
      }),
    );
  });

  it("downloads managed content as a CSRF-protected ZIP and reads its filename", async () => {
    setCsrfToken("csrf-download");
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(["zip"]), {
      status: 200,
      headers: {
        "content-type": "application/zip",
        "content-disposition": "attachment; filename*=UTF-8''%E8%B5%84%E6%96%99%E6%89%93%E5%8C%85.zip",
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.bulkDownloadManagedContent(["version-1", "version-2"]);
    expect(result.filename).toBe("资料打包.zip");
    expect(result.blob.size).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/content/bulk-download",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ version_ids: ["version-1", "version-2"] }),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-download" },
      }),
    );
  });

  it("downloads one managed media part and preserves the server filename", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(["markdown"]), {
      status: 200,
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "content-disposition": "attachment; filename*=UTF-8''training-transcript.md",
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.downloadManagedMedia("media/item", "transcript", "fallback.md");
    expect(result.filename).toBe("training-transcript.md");
    expect(result.blob.size).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/content/items/media%2Fitem/media-download?part=transcript",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("loads trash and restores managed content with CSRF protection", async () => {
    setCsrfToken("csrf-restore");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, status_counts: {} }))
      .mockResolvedValueOnce(jsonResponse({ item_id: "item-1", version_id: "version-1", restored_status: "approved" }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await api.managedContentTrash({ query: "标准", limit: 25, offset: 0 });
    await api.restoreManagedContent("item/1", "version-1", {
      target_category_id: "cat-04",
      replace_conflict_item_id: "item-2",
      replace_conflict_expected_version_id: "version-2",
    });
    await api.managedContentAuditEvents("item/1");

    expect(fetchMock).toHaveBeenNthCalledWith(1,
      "/api/admin/content/trash?query=%E6%A0%87%E5%87%86&limit=25&offset=0",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      "/api/admin/content/items/item%2F1/restore",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          expected_version_id: "version-1",
          target_category_id: "cat-04",
          replace_conflict_item_id: "item-2",
          replace_conflict_expected_version_id: "version-2",
        }),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-restore" },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(3,
      "/api/admin/content/items/item%2F1/audit-events",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
  });

  it("renames and renumbers managed folders through narrow CSRF-protected PATCH routes", async () => {
    setCsrfToken("csrf-category");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ id: "folder/1", display_name: "新目录", version: 3 }))
      .mockResolvedValueOnce(jsonResponse([{ id: "folder/1", display_code: "02", version: 4 }]));
    vi.stubGlobal("fetch", fetchMock);

    await api.renameManagedCategory("folder/1", { display_name: "新目录", expected_version: 2 });
    await api.updateManagedCategoryNumber("folder/1", {
      target_position: 2,
      confirm_number_shift: true,
      expected_version: 3,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(1,
      "/api/admin/content/categories/folder%2F1/name",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        body: JSON.stringify({ display_name: "新目录", expected_version: 2 }),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-category" },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      "/api/admin/content/categories/folder%2F1/number",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        body: JSON.stringify({ target_position: 2, confirm_number_shift: true, expected_version: 3 }),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-category" },
      }),
    );
  });

  it("normalizes managed-content review notes for single and bulk requests", async () => {
    setCsrfToken("csrf-review");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ version_id: "version-1" }))
      .mockResolvedValueOnce(jsonResponse({ results: [], succeeded: 0, failed: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.reviewManagedContent("version/1", false, "  请补充范围  ");
    await api.bulkReviewManagedContent(["version-1", "version-2"], false, "  统一退回  ");

    expect(fetchMock).toHaveBeenNthCalledWith(1,
      "/api/admin/content/versions/version%2F1/review",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approved: false, note: "请补充范围", category_id: null }),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-review" },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      "/api/admin/content/bulk-review",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ version_ids: ["version-1", "version-2"], approved: false, note: "统一退回" }),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-review" },
      }),
    );
  });

  it("regenerates a managed PPTX preview with CSRF protection", async () => {
    setCsrfToken("csrf-preview");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      version_id: "version/1",
      preview_parent_id: "parent-1",
      preview_status: "ready",
    }));
    vi.stubGlobal("fetch", fetchMock);

    await api.regenerateManagedContentPreview("version/1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/content/versions/version%2F1/preview",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({}),
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-preview" },
      }),
    );
  });

  it("preserves the administrator PATCH contract", async () => {
    setCsrfToken("csrf-admin");
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ id: 7, employee_id: "u7", real_name: "管理员测试", role: "admin", is_active: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.adminPatchUser(7, { role: "admin", is_active: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/users/7",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        body: JSON.stringify({ role: "admin", is_active: true }),
        headers: {
          "content-type": "application/json",
          "X-CSRF-Token": "csrf-admin",
        },
      }),
    );
  });

  it("serializes managed publication task filters for server-side pagination", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ jobs: [], total: 0, status_counts: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.managedContentIndexJobs({
      query: "施工 标准",
      category_id: "cat-03",
      doc_type: "pdf",
      source_origin: "legacy",
      status: "processing",
      history: true,
      include_archived: true,
      limit: 25,
      offset: 50,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/content/index-jobs?query=%E6%96%BD%E5%B7%A5+%E6%A0%87%E5%87%86&category_id=cat-03&doc_type=pdf&source_origin=legacy&status=processing&history=true&include_archived=true&limit=25&offset=50",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
  });

  it("invokes the 401 handler and exposes structured ApiError data", async () => {
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "需要重新登录" }), {
          status: 401,
          statusText: "Unauthorized",
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const error = await api.adminStats().catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 401,
      body: JSON.stringify({ detail: "需要重新登录" }),
    });
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it("refreshes auth only for forbidden managed-content requests", async () => {
    const forbidden = vi.fn();
    setContentPermissionForbiddenHandler(forbidden);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "无权限" }, 403)));

    await api.managedContentCapabilities().catch(() => undefined);
    await api.adminStats().catch(() => undefined);

    expect(forbidden).toHaveBeenCalledTimes(1);
  });

  it("refreshes auth for forbidden managed-content multipart uploads", async () => {
    const forbidden = vi.fn();
    setContentPermissionForbiddenHandler(forbidden);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "无权限" }, 403)));

    await api.uploadManagedContent([new File(["x"], "test.pdf")], "category-1").catch(() => undefined);

    expect(forbidden).toHaveBeenCalledTimes(1);
  });

  it("preserves explicit folder paths and marks folder uploads in FormData", async () => {
    setCsrfToken("csrf-folder");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ batch_id: "batch-1", entries: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const guide = new File(["guide"], "guide.md", { type: "text/markdown" });

    await api.uploadManagedContent(
      [{ file: guide, relativePath: "资料包/01 建筑/guide.md" }],
      "category-1",
      "folder",
      undefined,
      {
        allowFolderMerge: true,
        conflictActions: [{ strategy: "rename", filename: "guide (1).md" }],
      },
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const form = init.body as FormData;
    expect(form.getAll("files")).toEqual([guide]);
    expect(form.getAll("relative_paths")).toEqual(["资料包/01 建筑/guide.md"]);
    expect(form.get("category_id")).toBe("category-1");
    expect(form.get("upload_mode")).toBe("folder");
    expect(form.get("allow_folder_merge")).toBe("true");
    expect(form.getAll("conflict_actions")).toEqual([
      JSON.stringify({ strategy: "rename", filename: "guide (1).md" }),
    ]);
    expect(init.headers).toEqual({ "X-CSRF-Token": "csrf-folder" });
  });

  it("preflights managed uploads with relative paths and merge intent", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ entries: [], folder_conflicts: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const guide = new File(["guide"], "guide.md", { type: "text/markdown" });

    await api.preflightManagedContentUpload(
      [{ file: guide, relativePath: "资料包/guide.md" }],
      "category-1",
      "folder",
      true,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/content/uploads/preflight",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          category_id: "category-1",
          upload_mode: "folder",
          allow_folder_merge: true,
          entries: [{ filename: "guide.md", relative_path: "资料包/guide.md", size_bytes: guide.size }],
        }),
      }),
    );
  });

  it("keeps ordinary file uploads compatible with filename paths", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ batch_id: "batch-1", entries: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const guide = new File(["guide"], "guide.md");

    await api.uploadManagedContent([guide], "category-1");

    const form = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(form.getAll("relative_paths")).toEqual(["guide.md"]);
    expect(form.get("upload_mode")).toBe("files");
  });

  it("keeps mixed-upload video idempotency keys aligned with document slots", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ batch_id: "batch-video", entries: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const guide = new File(["guide"], "guide.md", { type: "text/markdown" });
    const video = new File(["video"], "training.mp4", { type: "video/mp4" });
    const requestKey = "11111111-1111-4111-8111-111111111111";

    await api.uploadManagedContent(
      [guide, video],
      "category-1",
      "files",
      undefined,
      {
        videoSchemeId: "scheme-1",
        videoIdempotencyKeys: ["", requestKey],
      },
    );

    const form = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(form.getAll("video_idempotency_keys")).toEqual(["", requestKey]);
    expect(form.get("video_scheme_id")).toBe("scheme-1");
  });

  it("serializes managed upload task filters and loads task details", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ tasks: [], total: 0, status_counts: {} }))
      .mockResolvedValueOnce(jsonResponse({ batch_id: "batch/1", entries: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.managedUploadTasks({ status: "failed", query: "规范", limit: 10, offset: 20 });
    await api.managedUploadTask("batch/1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/admin/content/upload-tasks?status=failed&query=%E8%A7%84%E8%8C%83&limit=10&offset=20",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/admin/content/upload-tasks/batch%2F1",
      expect.objectContaining({ credentials: "include", headers: {} }),
    );
  });

  it("preserves safe structured error code, message and retry policy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            detail: {
              code: "upload_idempotency_conflict",
              message: "本次提交与原上传请求不一致，请重新提交。",
              retryable: false,
            },
          },
          409,
        ),
      ),
    );

    const error = await api.adminStats().catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "upload_idempotency_conflict",
      message: "本次提交与原上传请求不一致，请重新提交。",
      retryable: false,
    });
  });
});

describe("Phase 4B transcription API contracts", () => {
  it("uses the administrator media endpoint and lists recoverable tasks", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await api.listMediaAssets();
    await api.listTranscriptionProfiles();
    await api.listTranscriptionSchemes();
    await api.listTranscriptionJobs(true, 25);

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/admin/media", expect.objectContaining({ credentials: "include" }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/admin/transcription/profiles", expect.objectContaining({ credentials: "include" }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/admin/transcription/schemes", expect.objectContaining({ credentials: "include" }));
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/admin/transcription/jobs?latest_per_media=true&limit=25", expect.objectContaining({ credentials: "include" }));
  });

  it("uploads automatic media as exact FormData without forcing Content-Type", async () => {
    setCsrfToken("csrf-asr");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ media_id: "media-1", transcription_job_id: "job-1" }));
    vi.stubGlobal("fetch", fetchMock);
    const video = new File(["video"], "training.mp4", { type: "video/mp4" });

    await api.uploadAutomaticMediaVideo(video, "培训视频", "profile-1", "11111111-1111-4111-8111-111111111111");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/admin/media");
    expect(init).toMatchObject({ method: "POST", credentials: "include", headers: { "X-CSRF-Token": "csrf-asr" } });
    expect(init.headers).not.toHaveProperty("content-type");
    const form = init.body as FormData;
    expect((form.get("video") as File).name).toBe("training.mp4");
    expect(form.get("title")).toBe("培训视频");
    expect(form.get("profile_id")).toBe("profile-1");
    expect(form.get("scheme_id")).toBe("profile-1");
    expect(form.get("request_idempotency_key")).toBe("11111111-1111-4111-8111-111111111111");
    expect(form.get("transcript")).toBeNull();
  });

  it("preflights media identities and sends directory-scoped upload options", async () => {
    setCsrfToken("csrf-media-conflict");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ category_id: "cat-05", entries: [] }))
      .mockResolvedValueOnce(jsonResponse({ media_id: "media-1" }));
    vi.stubGlobal("fetch", fetchMock);
    await api.preflightMediaUpload({
      category_id: "cat-05",
      items: [{ client_id: "client-1", title: "培训", original_filename: "training.mp4" }],
    });
    const video = new File(["video"], "training.mp4", { type: "video/mp4" });
    await api.uploadAutomaticMediaVideo(
      video,
      "培训 (1)",
      "profile-1",
      "11111111-1111-4111-8111-111111111111",
      undefined,
      { categoryId: "cat-05", originalFilename: "training (1).mp4" },
    );

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/media/preflight");
    const form = fetchMock.mock.calls[1][1].body as FormData;
    expect(form.get("category_id")).toBe("cat-05");
    expect(form.get("original_filename")).toBe("training (1).mp4");
  });

  it("binds a replacement upload to its source media and selected profile", async () => {
    setCsrfToken("csrf-replacement");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ media_id: "candidate-1", transcription_job_id: "job-1" }));
    vi.stubGlobal("fetch", fetchMock);
    const video = new File(["video-v2"], "training-v2.mp4", { type: "video/mp4" });

    await api.uploadReplacementMediaVideo(
      video,
      "培训视频",
      "profile-2",
      "22222222-2222-4222-8222-222222222222",
      "source-media-1",
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/admin/media");
    expect(init).toMatchObject({ method: "POST", credentials: "include", headers: { "X-CSRF-Token": "csrf-replacement" } });
    const form = init.body as FormData;
    expect((form.get("video") as File).name).toBe("training-v2.mp4");
    expect(form.get("title")).toBe("培训视频");
    expect(form.get("profile_id")).toBe("profile-2");
    expect(form.get("scheme_id")).toBe("profile-2");
    expect(form.get("request_idempotency_key")).toBe("22222222-2222-4222-8222-222222222222");
    expect(form.get("replacement_source_media_id")).toBe("source-media-1");
  });

  it("creates a media metadata revision with CSRF and an immutable expected version", async () => {
    setCsrfToken("csrf-metadata");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ version_id: "candidate-version" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.createMediaMetadataRevision(
      "media/1",
      "published-version",
      "更新后的标题",
      "updated.mp4",
      "33333333-3333-4333-8333-333333333333",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/transcription/media/media%2F1/metadata-revisions",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-metadata" },
        body: JSON.stringify({
          expected_version_id: "published-version",
          title: "更新后的标题",
          original_filename: "updated.mp4",
          request_idempotency_key: "33333333-3333-4333-8333-333333333333",
        }),
      }),
    );
  });

  it("reports multipart transfer progress before server-side preparation", async () => {
    class FakeXMLHttpRequest {
      static instance: FakeXMLHttpRequest;
      upload = {
        onprogress: null as ((event: ProgressEvent) => void) | null,
        onload: null as (() => void) | null,
      };
      status = 200;
      statusText = "OK";
      responseText = JSON.stringify({ media_id: "media-1", transcription_job_id: "job-1" });
      withCredentials = false;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      open = vi.fn();
      setRequestHeader = vi.fn();
      send = vi.fn();

      constructor() {
        FakeXMLHttpRequest.instance = this;
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
    setCsrfToken("csrf-asr");
    const onProgress = vi.fn();
    const onUploaded = vi.fn();
    const video = new File(["video"], "training.mp4", { type: "video/mp4" });

    const result = api.uploadAutomaticMediaVideo(
      video,
      "培训视频",
      "profile-1",
      "11111111-1111-4111-8111-111111111111",
      { onProgress, onUploaded },
    );
    const request = FakeXMLHttpRequest.instance;
    request.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent);
    request.upload.onload?.();
    request.onload?.();

    await expect(result).resolves.toMatchObject({ media_id: "media-1", transcription_job_id: "job-1" });
    expect(request.open).toHaveBeenCalledWith("POST", "/api/admin/media");
    expect(request.withCredentials).toBe(true);
    expect(request.setRequestHeader).toHaveBeenCalledWith("X-CSRF-Token", "csrf-asr");
    expect(request.send).toHaveBeenCalledWith(expect.any(FormData));
    expect(onProgress).toHaveBeenCalledWith({ loaded: 50, total: 100, ratio: 0.5 });
    expect(onUploaded).toHaveBeenCalledOnce();
  });

  it("preserves CSRF protection for cancellation and retry", async () => {
    setCsrfToken("csrf-asr");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ status: "cancelled" }))
      .mockResolvedValueOnce(jsonResponse({ status: "pending" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.cancelTranscriptionJob("job-1");
    await api.retryTranscription("media-1", "profile-1", "22222222-2222-4222-8222-222222222222");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/admin/transcription/jobs/job-1/cancel", expect.objectContaining({ method: "POST", headers: { "X-CSRF-Token": "csrf-asr" } }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/admin/transcription/media/media-1/retry", expect.objectContaining({
      method: "POST",
      headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-asr" },
      body: JSON.stringify({ profile_id: "profile-1", request_idempotency_key: "22222222-2222-4222-8222-222222222222" }),
    }));
  });
});


describe("Phase 5 transcript publication API contracts", () => {
  it("publishes with a strict empty body and CSRF while keeping reads side-effect free", async () => {
    setCsrfToken("csrf-phase5");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ version_id: "version-1", markdown: "正文", markdown_sha256: "a".repeat(64) }))
      .mockResolvedValueOnce(jsonResponse({ media_id: "media-1", version_id: "version-1", language: "zh-CN", duration_ms: 1000, segments: [] }))
      .mockResolvedValueOnce(jsonResponse({ version: {}, job: null, reused: false }, 202));
    vi.stubGlobal("fetch", fetchMock);

    await api.listTranscriptVersions("media-1");
    await api.previewTranscriptVersion("version-1");
    await api.previewTranscriptVersionTimeline("version-1");
    await api.publishTranscriptVersion("version-1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/admin/transcription/media/media-1/versions", expect.objectContaining({ credentials: "include", headers: {} }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/admin/transcription/versions/version-1/markdown", expect.objectContaining({ credentials: "include", headers: {} }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/admin/transcription/versions/version-1/timeline", expect.objectContaining({ credentials: "include", headers: {} }));
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/admin/transcription/versions/version-1/publish", expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({}),
      headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-phase5" },
    }));
  });

  it("creates a Markdown revision with base SHA, idempotency and CSRF", async () => {
    setCsrfToken("csrf-revision");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ version_id: "version-2" }, 201));
    vi.stubGlobal("fetch", fetchMock);

    await api.createTranscriptRevision(
      "version-1",
      "说话人 1 00:00:00\n修订内容\n",
      "a".repeat(64),
      "11111111-1111-4111-8111-111111111111",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/transcription/versions/version-1/revisions",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json", "X-CSRF-Token": "csrf-revision" },
        body: JSON.stringify({
          markdown: "说话人 1 00:00:00\n修订内容\n",
          base_markdown_sha256: "a".repeat(64),
          request_idempotency_key: "11111111-1111-4111-8111-111111111111",
        }),
      }),
    );
  });
});

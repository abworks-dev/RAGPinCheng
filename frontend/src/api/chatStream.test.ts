import { afterEach, describe, expect, it, vi } from "vitest";
import { setCsrfToken } from "./client";
import { streamChat } from "./chatStream";

function responseWithStream(chunks: string[]) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, status: 200, statusText: "OK", body: stream } as Response;
}

afterEach(() => {
  setCsrfToken(null);
  vi.unstubAllGlobals();
});

describe("streamChat", () => {
  it("parses split CRLF lines, split JSON and multiple events in one chunk", async () => {
    setCsrfToken("csrf-test");
    const fetchMock = vi.fn().mockResolvedValue(
      responseWithStream([
        "event: prep\r",
        '\ndata: {"search_query":"规范","rewrite_applied":false}\n\nevent: token\ndata: {"te',
        'xt":"你"}\n\nevent: token\ndata: {"text":"好"}\n\nevent: done\ndata: {"answer_text":"你好","timings":{},"sources":[],"history_chars":0,"budget":1}\n\n',
        'event: error\ndata: {"message":"后端错误"}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const events = [];
    for await (const event of streamChat("conversation-1", { query: "问题" })) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual(["prep", "token", "token", "done", "error"]);
    expect(events[1]).toEqual({ type: "token", data: { text: "你" } });
    expect(events[2]).toEqual({ type: "token", data: { text: "好" } });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/conversations/conversation-1/chat",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        signal: undefined,
        headers: {
          accept: "text/event-stream",
          "content-type": "application/json",
          "X-CSRF-Token": "csrf-test",
        },
        body: JSON.stringify({ query: "问题" }),
      }),
    );
  });

  it("passes AbortSignal through to fetch", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(responseWithStream([]));
    vi.stubGlobal("fetch", fetchMock);

    const iterator = streamChat("conversation-2", { query: "问题" }, controller.signal);
    await iterator.next();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/conversations/conversation-2/chat",
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it("throws when the response is not a usable SSE stream", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        body: null,
        text: async () => "failure",
      }),
    );

    await expect(streamChat("conversation-3", { query: "问题" }).next()).rejects.toThrow(
      "chat failed: 500 Server Error failure",
    );
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PdfPreviewProvider, usePdfPreview } from "../hooks/usePdfPreview";
import { calculatePdfScale, getPdfPrefetchOrder, PdfPreview } from "./PdfPreview";

const pdfMocks = vi.hoisted(() => ({
  getOperatorList: vi.fn(),
  getPage: vi.fn(),
}));

vi.mock("react-pdf", () => ({
  pdfjs: {
    version: "test",
    GlobalWorkerOptions: { workerSrc: "" },
  },
  Document: ({ children, onLoadSuccess, options }: any) => {
    return (
      <div>
        <button
          type="button"
          onClick={() => onLoadSuccess({ numPages: 6, getPage: pdfMocks.getPage })}
        >
          模拟 PDF 加载
        </button>
        <output aria-label="PDF 加载选项">{JSON.stringify(options)}</output>
        {children}
      </div>
    );
  },
  Page: ({ onLoadSuccess, pageNumber, scale }: any) => {
    return (
      <>
        <button
          type="button"
          onClick={() => onLoadSuccess({ getViewport: () => ({ width: 600, height: 800 }) })}
        >
          模拟页面加载
        </button>
        <output aria-label="可见页码">{pageNumber}</output>
        <output aria-label="渲染缩放">{scale}</output>
      </>
    );
  },
}));

class ResizeObserverMock {
  observe() {}
  disconnect() {}
  unobserve() {}
}

function OpenPreview() {
  const { open } = usePdfPreview();
  return (
    <button type="button" onClick={() => open("pdf-1", "测试规范", "pdf", 1)}>
      打开
    </button>
  );
}

function ReturnTargetProbe() {
  const { state, open, close } = usePdfPreview();
  return (
    <>
      <output role="status" aria-label="预览返回目标">{state.returnTo || "none"}</output>
      <button type="button" onClick={() => open("pdf-1", "测试规范", "pdf", 1, {}, "managed-content-detail")}>打开详情预览</button>
      <button type="button" onClick={close}>关闭预览</button>
    </>
  );
}

function renderPreview() {
  render(
    <PdfPreviewProvider>
      <OpenPreview />
      <PdfPreview />
    </PdfPreviewProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "打开" }));
  fireEvent.click(screen.getByRole("button", { name: "模拟 PDF 加载" }));
  fireEvent.click(screen.getByRole("button", { name: "模拟页面加载" }));
}

describe("calculatePdfScale", () => {
  it("fits the whole page inside the available viewport", () => {
    expect(calculatePdfScale("fit-page", { width: 932, height: 732 }, { width: 600, height: 800 }))
      .toBeCloseTo(0.875);
  });

  it("fits width independently and keeps supported bounds", () => {
    expect(calculatePdfScale("fit-width", { width: 632, height: 400 }, { width: 600, height: 800 })).toBe(1);
    expect(calculatePdfScale("fit-width", { width: 100, height: 100 }, { width: 600, height: 800 })).toBe(0.5);
    expect(calculatePdfScale("actual", { width: 100, height: 100 }, { width: 600, height: 800 })).toBe(1);
  });
});

describe("getPdfPrefetchOrder", () => {
  it("prefetches two forward pages before two previous pages", () => {
    expect(getPdfPrefetchOrder(4, 10)).toEqual([5, 6, 3, 2]);
  });

  it("keeps the window inside document bounds", () => {
    expect(getPdfPrefetchOrder(1, 3)).toEqual([2, 3]);
    expect(getPdfPrefetchOrder(3, 3)).toEqual([2, 1]);
  });
});

describe("PdfPreview interactions", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", ResizeObserverMock);
    pdfMocks.getOperatorList.mockReset().mockResolvedValue({});
    pdfMocks.getPage.mockReset().mockImplementation(async () => ({
      getOperatorList: pdfMocks.getOperatorList,
    }));
  });

  it("opens in fit-page and switches to custom zoom", () => {
    renderPreview();

    expect(screen.getByRole("combobox", { name: "缩放模式" })).toHaveValue("fit-page");
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByRole("combobox", { name: "缩放模式" })).toHaveValue("custom");
    expect(screen.getByText("110%")).toBeInTheDocument();
  });

  it("tracks and clears the managed content return target", () => {
    render(
      <PdfPreviewProvider>
        <ReturnTargetProbe />
      </PdfPreviewProvider>,
    );

    expect(screen.getByRole("status", { name: "预览返回目标" })).toHaveTextContent("none");
    fireEvent.click(screen.getByRole("button", { name: "打开详情预览" }));
    expect(screen.getByRole("status", { name: "预览返回目标" })).toHaveTextContent("managed-content-detail");
    fireEvent.click(screen.getByRole("button", { name: "关闭预览" }));
    expect(screen.getByRole("status", { name: "预览返回目标" })).toHaveTextContent("none");
  });

  it("switches between hand panning and text selection", () => {
    renderPreview();

    const toggle = screen.getByRole("button", { name: "切换到文字选择" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "切换到手形拖动" })).toHaveAttribute("aria-pressed", "false");
  });

  it("navigates pages with buttons and arrow keys", async () => {
    renderPreview();

    fireEvent.click(await screen.findByRole("button", { name: "下一页 →" }));
    expect(screen.getByRole("spinbutton", { name: "页码" })).toHaveValue(2);
    fireEvent.keyDown(screen.getByRole("region", { name: "PDF 页面" }), { key: "ArrowRight" });
    expect(screen.getByRole("spinbutton", { name: "页码" })).toHaveValue(3);
  });

  it("uses range loading and dynamically prefetches the adjacent window", async () => {
    renderPreview();

    expect(JSON.parse(screen.getByLabelText("PDF 加载选项").textContent || "{}"))
      .toEqual({ disableAutoFetch: true, disableStream: true, rangeChunkSize: 256 * 1024 });

    await waitFor(() => expect(pdfMocks.getPage.mock.calls.map(([page]) => page)).toEqual([2, 3]));

    fireEvent.click(screen.getByRole("button", { name: "下一页 →" }));
    await waitFor(() => expect(pdfMocks.getPage.mock.calls.map(([page]) => page)).toEqual([2, 3, 4, 1]));

    expect(screen.getAllByLabelText("可见页码")).toHaveLength(1);
    expect(screen.getByLabelText("可见页码")).toHaveTextContent("2");
  });

  it("drops stale queued pages when the user jumps while prefetch is in flight", async () => {
    let releaseFirstPage!: () => void;
    const firstPage = new Promise<{ getOperatorList: () => Promise<unknown> }>((resolve) => {
      releaseFirstPage = () => resolve({ getOperatorList: pdfMocks.getOperatorList });
    });
    pdfMocks.getPage
      .mockReset()
      .mockImplementationOnce(() => firstPage)
      .mockImplementation(async () => ({ getOperatorList: pdfMocks.getOperatorList }));

    renderPreview();
    await waitFor(() => expect(pdfMocks.getPage.mock.calls.map(([page]) => page)).toEqual([2]));

    fireEvent.change(screen.getByRole("spinbutton", { name: "页码" }), { target: { value: "4" } });
    releaseFirstPage();

    await waitFor(() => {
      expect(pdfMocks.getPage.mock.calls.map(([page]) => page)).toEqual([2, 5, 6, 3]);
    });
  });
});

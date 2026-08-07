import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PdfPreviewProvider, usePdfPreview } from "../hooks/usePdfPreview";
import { calculatePdfScale, PdfPreview } from "./PdfPreview";

vi.mock("react-pdf", () => ({
  pdfjs: {
    version: "test",
    GlobalWorkerOptions: { workerSrc: "" },
  },
  Document: ({ children, onLoadSuccess }: any) => {
    return (
      <div>
        <button type="button" onClick={() => onLoadSuccess({ numPages: 3 })}>模拟 PDF 加载</button>
        {children}
      </div>
    );
  },
  Page: ({ onLoadSuccess, scale }: any) => {
    return (
      <>
        <button
          type="button"
          onClick={() => onLoadSuccess({ getViewport: () => ({ width: 600, height: 800 }) })}
        >
          模拟页面加载
        </button>
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

describe("PdfPreview interactions", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  });

  it("opens in fit-page and switches to custom zoom", () => {
    renderPreview();

    expect(screen.getByRole("combobox", { name: "缩放模式" })).toHaveValue("fit-page");
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByRole("combobox", { name: "缩放模式" })).toHaveValue("custom");
    expect(screen.getByText("110%")).toBeInTheDocument();
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
});

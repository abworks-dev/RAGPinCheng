import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptionWorkbenchSheet } from "./TranscriptionWorkbenchSheet";

vi.mock("./TranscriptionVersionPanel", () => ({
  TranscriptionVersionPanel: ({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) => (
    <button type="button" onClick={() => onDirtyChange?.(true)}>制造未保存修改</button>
  ),
}));

describe("TranscriptionWorkbenchSheet", () => {
  it("keeps the sheet open when the user cancels closing dirty content", () => {
    const onClose = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <TranscriptionWorkbenchSheet
        open
        title="校对测试"
        originalFilename="fixture.mp4"
        mediaId="media-1"
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "制造未保存修改" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭转写工作台" }));

    expect(confirm).toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

import { describe, expect, it } from "vitest";
import { formatMessageTime } from "./message-time";

const now = new Date(2026, 7, 19, 15, 4, 0);
const seconds = (date: Date) => Math.floor(date.getTime() / 1000);

describe("formatMessageTime", () => {
  it.each([
    [new Date(2026, 7, 19, 9, 7), "09:07"],
    [new Date(2026, 7, 18, 22, 8), "昨天 22:08"],
    [new Date(2026, 7, 17, 1, 2), "前天 01:02"],
    [new Date(2026, 2, 3, 8, 9), "3月3日 08:09"],
    [new Date(2025, 11, 31, 23, 59), "2025年12月31日 23:59"],
  ])("formats date", (date, expected) => {
    expect(formatMessageTime(seconds(date), now)).toBe(expected);
  });

  it("compares calendar dates independently of daylight-saving day length", () => {
    const beforeDstChange = new Date(2026, 2, 7, 12, 0);
    const afterDstChange = new Date(2026, 2, 9, 12, 0);
    expect(formatMessageTime(seconds(beforeDstChange), afterDstChange)).toBe("前天 12:00");
  });
});

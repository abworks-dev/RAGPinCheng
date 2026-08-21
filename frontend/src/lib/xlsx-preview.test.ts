import { describe, expect, it } from "vitest";
import ExcelJS from "exceljs";

import { parseSpreadsheetPreview } from "./xlsx-preview";

async function styledWorkbook(): Promise<ArrayBuffer> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("核查要点");
  sheet.mergeCells("A1:C1");
  sheet.getCell("A1").value = "地下室机电吊架出图审核要点表";
  sheet.getCell("A1").font = { name: "Microsoft YaHei", size: 18, bold: true, color: { argb: "FF123456" } };
  sheet.getCell("A1").fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFFFFF00" } };
  sheet.getCell("A1").alignment = { horizontal: "center", vertical: "middle" };
  sheet.getCell("A1").border = { bottom: { style: "thin", color: { argb: "FF000000" } } };
  sheet.getColumn(1).width = 24;
  sheet.getRow(1).height = 30;
  sheet.getCell("A2").value = 0.125;
  sheet.getCell("A2").numFmt = "0.0%";
  sheet.getCell("B2").value = 1234567.5;
  sheet.getCell("B2").numFmt = "#,##0.00";
  sheet.getCell("C2").value = new Date(2026, 7, 21);
  sheet.getCell("C2").numFmt = "yyyy-mm-dd";
  workbook.addWorksheet("机电模型");
  const buffer = await workbook.xlsx.writeBuffer();
  return Uint8Array.from(buffer as unknown as ArrayLike<number>).buffer;
}

describe("parseSpreadsheetPreview", () => {
  it("preserves source sheet names without inventing Sheet1", async () => {
    const preview = await parseSpreadsheetPreview(await styledWorkbook());

    expect(preview.map((sheet) => sheet.name)).toEqual(["核查要点", "机电模型"]);
  });

  it("preserves merged cells and common workbook styling", async () => {
    const [sheet] = await parseSpreadsheetPreview(await styledWorkbook());
    const title = sheet.rows[0].cells[0];

    expect(title).toMatchObject({ rowSpan: 1, colSpan: 3, text: "地下室机电吊架出图审核要点表" });
    expect(title.style).toMatchObject({
      fontFamily: "Microsoft YaHei",
      fontSize: "18pt",
      fontWeight: "700",
      color: "#123456",
      backgroundColor: "#ffff00",
      textAlign: "center",
      verticalAlign: "middle",
      borderBottom: "1px solid #000000",
    });
    expect(sheet.columnWidths[0]).toBeGreaterThan(150);
    expect(sheet.rows[0].height).toBe(40);
    expect(sheet.rows[1].cells[0].text).toBe("12.5%");
    expect(sheet.rows[1].cells[1].text).toBe("1,234,567.50");
    expect(sheet.rows[1].cells[2].text).toBe("2026-08-21");
  });
});

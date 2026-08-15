import { defineConfig } from "@playwright/test";
import process from "node:process";

const viewports = [
  { name: "chromium-1440x900", width: 1440, height: 900 },
  { name: "chromium-1280x720", width: 1280, height: 720 },
  { name: "chromium-768x1024", width: 768, height: 1024 },
  { name: "chromium-390x844", width: 390, height: 844 },
];
const testPort = process.env.PLAYWRIGHT_PORT || "4173";

export default defineConfig({
  testDir: "./tests/visual",
  outputDir: "./test-results/visual",
  snapshotPathTemplate: `{testDir}/../visual-baseline/${process.platform}/{projectName}/{testFilePath}/{arg}{ext}`,
  fullyParallel: true,
  workers: 2,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI
    ? [["line"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
    },
  },
  use: {
    baseURL: `http://127.0.0.1:${testPort}`,
    browserName: "chromium",
    colorScheme: "light",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: viewports.map(({ name, width, height }) => ({
    name,
    use: { viewport: { width, height } },
  })),
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${testPort} --strictPort`,
    url: `http://127.0.0.1:${testPort}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});

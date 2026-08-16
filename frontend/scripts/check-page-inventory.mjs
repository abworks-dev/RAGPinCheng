import fs from "node:fs";
import path from "node:path";

const frontendRoot = process.cwd();
const repoRoot = path.resolve(frontendRoot, "..");
const inventoryPath = path.join(repoRoot, "docs", "design", "page-inventory.md");
const inventory = fs.readFileSync(inventoryPath, "utf8");
const appSource = fs.readFileSync(path.join(frontendRoot, "src", "App.tsx"), "utf8");

function listFiles(root) {
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...listFiles(absolute));
    else files.push(path.relative(frontendRoot, absolute).replaceAll("\\", "/"));
  }
  return files;
}

const frontendFiles = listFiles(frontendRoot);
const rows = inventory.split(/\r?\n/).filter((line) => line.startsWith("|") && !line.startsWith("|---") && !line.includes("页面 / tab"));
const errors = [];

for (const row of rows) {
  const columns = row.split("|").slice(1, -1).map((value) => value.trim());
  if (columns.length !== 10) {
    errors.push(`页面清单列数错误: ${row}`);
    continue;
  }
  const [page, route, , , , , , unit, visual, manual] = columns;
  const routeValue = route.replaceAll("`", "").trim();
  const appRoute = routeValue.startsWith("/admin/") ? routeValue.replace("/admin/", "") : routeValue;
  if (!appSource.includes(`path="${appRoute}"`)) {
    errors.push(`${page}: 路由未在 App.tsx 找到 ${routeValue}`);
  }

  const unitRefs = [...unit.matchAll(/([\w./*-]+\.test\.[\w]+)/g)].map((match) => match[1]);
  if (unitRefs.length === 0) errors.push(`${page}: unit test 列没有测试入口`);
  for (const ref of unitRefs) {
    const normalized = ref.replace(/^frontend\//, "");
    const exists = normalized.includes("/")
      ? frontendFiles.some((file) => {
          const pattern = new RegExp(`^${normalized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replaceAll("\\*\\*", ".*").replaceAll("\\*", "[^/]*")}$`);
          return pattern.test(file);
        })
      : frontendFiles.some((file) => file.endsWith(`/${normalized}`));
    if (!exists) errors.push(`${page}: unit test 不存在 ${ref}`);
  }

  const visualRefs = [...visual.matchAll(/([\w-]+\.spec\.ts)/g)].map((match) => match[1]);
  if (visualRefs.length === 0) errors.push(`${page}: visual spec 列没有入口`);
  for (const ref of visualRefs) {
    if (!frontendFiles.some((file) => file.endsWith(`/visual/${ref}`))) errors.push(`${page}: visual spec 不存在 ${ref}`);
  }

  const links = [...manual.matchAll(/\]\(([^)]+)\)/g)].map((match) => match[1]);
  if (links.length === 0) errors.push(`${page}: 人工验收列没有链接`);
  for (const link of links) {
    const target = path.resolve(path.dirname(inventoryPath), link);
    if (!fs.existsSync(target)) errors.push(`${page}: 人工验收入口不存在 ${link}`);
  }
}

if (errors.length > 0) {
  console.error(errors.join("\n"));
  process.exit(1);
}

console.log(`page inventory ok: ${rows.length} pages`);

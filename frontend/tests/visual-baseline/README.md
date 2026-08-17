# 浏览器视觉测试与基线

本目录只保存合成数据生成、经过人工审查的 golden screenshots。Playwright 不连接后端，不使用真实账号、Cookie、密码、客户文件或生产截图；所有 `/api/**` 请求均由 `tests/visual/fixtures/` 拦截，未声明请求会直接使测试失败。

## 当前基线状态

- `login-*.png`、`register-*.png` 是早期匿名页面诊断截图，不属于 Playwright golden。
- 资料管理 Windows golden 覆盖正常列表、搜索筛选展开层、批量操作菜单和移入回收站确认四种状态，并在 `1440x900`、`1280x720`、`768x1024`、`390x844` 四个 viewport 下人工检查目录上下文、单项与批量操作、弹窗内容、遮挡和横向溢出，属于 accepted golden。分类设置截图沿用独立维护的 accepted golden。
- 对应的资料管理 Linux golden 由 WSL2 Ubuntu Chromium 实际截图生成，并按相同状态和 viewport 逐张检查后接受；未复制 Windows 图片。
- 索引任务保存整页和资料库发布任务区域两组 Windows golden；合成数据覆盖长文件名、处理中、已发布、发布失败、来源、内容块和结构化失败原因，并按相同四个 viewport 检查桌面表格与窄屏对象列表。Linux 继续执行结构与布局检查，但新增精确像素基线须在 Linux Chromium 上人工接受后启用，不得复制 Windows 图片。
- Playwright golden 按 `<platform>/<project>/<spec>/<页面>-<状态>-<宽>x<高>.png` 保存。Windows 与 Linux 使用各自人工审查的精确像素基线，不使用跨平台容差。

## 固定环境

- 浏览器：Playwright 管理的 Chromium；实际版本用 `npx playwright --version` 和浏览器测试报告记录。
- 主题/语言/时区：light、`zh-CN`、`Asia/Shanghai`。
- viewport：`1440x900`、`1280x720`、`768x1024`、`390x844`。
- 动效：`prefers-reduced-motion: reduce`，截图时禁用 CSS 动画并隐藏输入光标。

## 本地命令

在 `frontend/` 执行：

```text
npx playwright install chromium
npm run test:visual
npm run test:visual:headed
npm run test:visual:ui
```

只运行非像素行为、状态和布局检查：

```text
npm run test:visual -- admin-workflows.spec.ts
```

界面变更后，先生成候选截图：

```text
npm run test:visual:update -- admin-golden.spec.ts
```

更新命令不是接受命令。审查者必须逐张检查合成数据、页面完整性、关键操作可见性、遮挡、横向溢出和四个 viewport 的一致性，再审查 Git diff。不得在 CI 自动执行 `--update-snapshots`。

## 失败产物

失败时 `test-results/visual/` 保存截图和 trace，`playwright-report/` 保存 HTML 报告。这些运行产物不作为 golden 提交；CI 启用后仅在失败时作为 artifact 上传。

## Fixture 边界

隔离层仅模拟：当前管理员、资料权限、受管资料能力、分类、权限用户、资料条目，以及对应 mutation。fixture 使用 `TEST-*` 标识和“合成”中文名称，不包含真实身份或业务资料。测试不得回退到 Vite proxy 或任何外部服务。

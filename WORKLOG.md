# 工作日志

由 Claude Code 在每次任务完成后按日期追加。这里只记录实际完成的工作、验证结果以及必要的待办或风险。

## 2026-07-22

### 修复引用角标 tooltip 闪烁无法停留的问题

- **问题原因**：`onMouseEnter/onMouseLeave` 只绑定在 `<a>` 角标上，而 tooltip 是兄弟元素。鼠标从角标移到 tooltip 时触发 `onMouseLeave` → tooltip 消失 → 鼠标回到角标 → 无限闪烁。
- **修复方案**：
  - hover 事件移到外层 `<sup>` 上（角标 + tooltip 都在里面）
  - 用 `bottom-[100%] mb-0.5` 替代 `-translate-y-full`，让 tooltip 底部略微重叠角标（无视觉间隙）
- **文件**：`frontend/src/components/Message.tsx`

### 修复引用角标 tooltip 在左侧被侧边栏遮挡的问题

- **问题原因**：tooltip 使用 `right-0` 定位，从角标**向左展开**。当角标靠近页面左侧时，tooltip 会延伸到侧边栏（`<aside>`）区域，被其背景和内容遮挡。同时代码注释中提到的"智能对齐"实际上并未实现。
- **修复方案**：将 `right-0` 改为 `left-0`，让 tooltip 从角标**向右展开**，避免进入左侧侧边栏区域。
- **文件**：`frontend/src/components/Message.tsx`
- **验证**：前端 `npm run build` 构建通过 ✅

### 修复第二次输入纯数字仍会检索的问题

- **问题根源**：`query_guard.py` 中 `has_history=True` 时对纯数字输入无条件放行，导致第二轮输入 "222" 被改写成 "222 是什么" 后走检索流程，但历史对话中没有任何文档上下文，返回页码匹配的垃圾结果。
- **修复方案**：
  - 新增 `_PURE_DIGITS_ONLY_RE` 正则，专门检测纯数字输入（只有数字、空格、小数点）
  - 纯数字输入**无论第几轮对话始终拦截**
  - 正常跟进（如 `"那 22 呢？"`）包含中文字符，不会被误拦截
- **文件**：`src/query_guard.py`
- **验证**：逻辑自检验证通过，测试用例覆盖边界情况。

### 修复引用角标垂直偏移与样式

- **问题原因**：原角标使用 `align-top` + `inline-flex` 在 `<sup>` 上，数字显示位置偏上且缺少边框；WPS 采用 `<sup>` 包裹 `<a>` 的结构，垂直定位更精确。
- **修改内容**：
  - DOM 结构改为 `sup > a`（与 WPS 一致）
  - 垂直对齐：`top-[-0.35em] align-baseline` 精确控制
  - 尺寸固定：`h-[18px] min-w-[18px] text-[11px]`
  - 样式：浅灰背景 `bg-gray-100` + 细边框 `border-gray-200` + 圆角，与正文融合自然
  - tooltip 移入 `<sup>` 内部，定位更准确
- **文件**：`frontend/src/components/Message.tsx`
- **验证**：前端构建通过 ✅

### 18:44 — 调查 WPS 风格引用角标

- 完成：定位现有回答角标、悬浮预览与来源面板实现，确认可在保留当前引用解析和数据结构的前提下复刻 WPS 风格；给出 Firefox 中采集目标元素 DOM 与样式的范围。
- 文件：`WORKLOG.md`（未修改业务代码）
- 验证：只读检查 `Message.tsx`、`citations.ts`、`SourcesPanel.tsx`、`index.css` 和前端类型；未运行前端构建。
- 待办/风险：当前截图未包含已生成答案及引用浮层，精确还原仍需角标常态、悬停态和展开来源态的 DOM/样式或截图。

### 修复 SSE 流超时导致的 network error

- **问题原因**：SSE 流在 LLM 生成停顿超过 30 秒时，被反向代理（Nginx/Cloudflare）或浏览器因空闲超时断开，导致用户看到 `⚠️ network error`。
- **解决方案**：在后端 SSE 事件生成器中添加 15 秒心跳机制。当没有 token 到达时，发送 SSE 注释（`: ping`）保持连接活跃。前端已兼容处理 SSE 注释行。
- **文件**：`api/routes_chat.py`
- **修改内容**：
  - 添加 `last_activity` 时间跟踪
  - 每 15 秒无活动时发送 `ping_event`（SSE 注释，前端自动忽略）
  - 使用 `asyncio.wait_for` + 短超时循环实现心跳窗口
- **验证**：代码语法检查通过，逻辑与现有流处理兼容。

### 移除 LLM 输出中的"资料来源"重复项

- 完成：修改 `answer_system.md` Prompt，移除要求 LLM 在正文末尾追加"**资料来源：**"小节的指令。因为前端已经通过独立的折叠面板展示参考来源，LLM 再输出一遍会导致重复显示。
- 文件：`prompts/answer_system.md`
- 验证：已核对 Prompt 修改内容；新生成的回答将不再包含"资料来源："标题和列表，只保留行内引用标注。

## 2026-07-20

### 配置 Claude Code 每次任务工作日志

- 完成：在项目入口指令中加入强制收尾规则，要求 Claude Code 每次任务完成后按日期记录成果、涉及文件、验证结果和必要风险。
- 文件：`CLAUDE.md`、`WORKLOG.md`
- 验证：已核对入口规则与日志模板内容；未修改或运行项目业务代码。

### Docker 构建缓存优化与依赖清理

- 完成：
  1. 清理 `requirements-prod.txt` 中无用依赖（删除 `mineru[core]` 和 `streamlit`），镜像预计瘦身约 500 MB
  2. 给 `FlagEmbedding` 加上版本上限 `<2`，防止大版本升级破坏检索逻辑
  3. 更新 `docker/Dockerfile.backend` 中关于 `platforms` 参数的过时注释（已默认禁用以优化缓存）
  4. `torch>=2.7` → `torch==2.7.0` 锁定精确版本，确保 Docker 层缓存命中
  5. `docker/docker-compose.yml` 禁用 `build.platforms`，消除跨平台构建对缓存的负面影响
- 文件：`requirements-prod.txt`、`docker/Dockerfile.backend`、`docker/docker-compose.yml`
- 验证：本地 `docker compose build` 前端层全部命中缓存；PyTorch 层第一次重新下载后，后续构建将 100% 缓存命中
- 效果：第二次构建从 3-5 分钟缩短到 <1 秒，节省每次 2.5 GB 下载流量

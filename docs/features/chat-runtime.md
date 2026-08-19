# 对话运行时

- 状态：已实现
- 最后核对：2026-08-19

## 用户可观察能力

登录用户可以创建、恢复、连续追问和删除对话；回答通过 SSE 流式返回，并在完成后持久化消息、来源和会话检索状态。用户可按管理员开放的分类目录筛选企业知识范围；选择父目录会覆盖其中所有已启用的子目录。用户可复制提问、原位编辑最后一条提问，并可对最后一轮回答重新生成、查看保留的历史回答版本。

## 当前边界

### 已实现

- `ChatSession` 统一编排查询守卫、重写、检索、上下文携带、预算和生成；
- HTTP 请求通过 `api/conversation_runtime.py` 恢复会话、按会话加锁并持久化；
- 同步与流式路径共享生成准备和状态收尾逻辑；
- SSE 返回准备、正文、完成和错误状态，并保持来源 DTO 一致；`prep.used_sources`
  表示生成时使用的上下文候选，`done.sources` 及持久化来源只包含最终正文实际引用的资料。
- SSE `prep.relevance` 携带脱敏检索置信度快照；默认关闭的相关性门禁命中时，
  不调用回答模型，`done.finish_reason=retrieval_low_confidence`，且低相关来源不进入历史。
- 系统管理员可在“回答策略”页面统一调整回答温度、最大输出 Token、上下文字符上限和相关性门禁阈值；策略按请求读取，正在进行的流不受后续保存影响。
- 最后一轮助手回答可重新生成；原回答作为只读版本保留，后续上下文只采用当前有效版本；
- 已有后续追问的历史回答禁止重新生成，避免改变后续对话所依赖的上下文。
- 最后一条用户提问可在消息气泡中原位编辑；编辑会重新执行完整检索与生成，成功后原子切换问题和回答的有效版本，原始正文与历史版本继续保留；
- 编辑过的提问在气泡操作栏右侧提供问题版本导航；切换问题版本时同步展示该问题对应的回答版本，浏览非活动问题时禁止重新生成；
- 已有后续追问的历史提问禁止编辑；管理面对话详情只读展示当前内容，并可展开查看问题编辑记录及其对应回答。
- 管理员可配置历史对话自动清理的启用状态和保留天数；默认启用并保留 30 天，删除时由数据库外键级联清理消息与回答版本。
- 每个新回答、拒答和查询守卫结果都会在 `message_answer_versions` 保存策略版本与 JSON 快照；旧版本允许为空，便于线上排查和回答版本审计。
- `GET /api/knowledge-scopes` 只向登录用户返回活动、已纳入问答且允许单独筛选的目录；聊天请求提交稳定目录 ID，后端重新校验并展开活动且已纳入问答的子目录。
- 未指定目录时按当前全部已纳入问答的活动目录检索；若管理员关闭了全部目录，聊天按无来源处理，不能退化为无条件检索。

### 未实现

- TODO 中的跨父文档长期上下文增强。
- 查询拆分 Phase 2 的**生产灰度**：核心实现已落（`9bf065a` + `61c8961`，
  默认关闭），但完整 ChatSession 开关 A/B、上下文打包、回答质量、延迟与成本归 Phase B，
  在 Phase A 离线评测（见 `retrieval-pipeline.md` §验证）量化收益通过前不开启。

## 入口与调用链

```text
POST /api/conversations/{id}/chat
→ require_csrf
→ conversation lock
→ hydrate_session
→ resolve_category_scope
→ ChatSession.ask_stream
→ retrieve_for_turn
→ stream_generate
→ wrap_stream_with_persistence
→ messages / conversations in app.sqlite
```

重新生成仍使用同一聊天入口，通过 `regenerate_assistant_message_id` 指定目标。提问编辑通过 `edit_user_message_id` 与新 `query` 指定目标。后端均在会话锁内从目标轮之前恢复 `ChatSession`，重新执行既有检索与生成编排；只有流成功收尾后才在同一事务中切换有效版本。

## 关键文件

- `api/routes_chat.py`
- `api/conversation_runtime.py`
- `src/session.py`
- `src/generate.py`
- `api/schemas.py`
- `frontend/src/api/client.ts`

## 数据契约

- `SessionState.messages`、`last_sources`、`last_search_query`、`turn_index`；
- `StreamingTurnPrep`、`TurnResult`；
- SSE `prep`、文本、`done`、`error` 事件；
- SSE `done.assistant_message_id` 将前端临时消息 ID 替换为持久化 ID，使回答完成后可立即重新生成；
- `messages.sources_json` 只供 UI 恢复，不重新进入 LLM 上下文。
- 最终正文中的数字引用会按首次出现顺序连续重编号，并与公开来源数组保持
  `sources[N-1]` 对齐；无有效引用的回答不公开检索候选作为回答依据。
- `message_answer_versions` 保存回答版本及对应来源/检索状态快照；
- `message_answer_heads` 指向每轮当前有效回答；
- `message_turn_requests` 保存重新生成和提问编辑时需要复用的原始目录 ID；旧会话中的分类名称仍可解析，缺失范围时按当前全部已纳入问答目录兼容。
- `message_user_versions` 保存用户提问版本，`message_user_heads` 指向当前有效问题；
- `message_answer_versions.user_version_id` 将编辑后生成的回答关联到对应问题版本；旧回答为空时按原始问题兼容。
- `message_answer_versions.policy_version` 与 `policy_json` 保存生成该回答版本时的策略快照；`answer_policy_settings` 保存全局单例，`answer_policy_audit` 保存管理员变更前后值和原因。
- `maintenance_settings` 保存历史对话保留策略，`maintenance_runs` 保存不含对话正文和用户身份的运行统计。

## 依赖与下游消费者

- 依赖检索、回答生成、认证、app.sqlite；
- 下游包括前端流读取器、消息列表、引用和反馈。

## 不变量与安全边界

- 新入口不得复制或绕过 `ChatSession` 编排；
- HTTP 聊天必须经过 `conversation_runtime.py` 的恢复、锁和持久化；
- 重新生成只能作用于最后一轮，不增加 `turn_index`，不得覆盖基础消息正文；
- 提问编辑只能作用于最后一轮，不增加 `turn_index`，不得覆盖 `messages.content`；编辑失败不得切换问题或回答活动指针；
- 后续追问和会话恢复只将当前有效回答版本放入 LLM 历史；
- 客户端目录名称不构成授权或过滤依据；聊天、重新生成和提问编辑都必须在服务端把稳定目录 ID 或旧名称重新解析为当前允许的 `category_key`；
- 切换知识范围时，历史来源只有在 `category_key` 或兼容分类名称仍属于当前范围时才可携带；
- 流必须被完整消费或关闭，才能可靠完成状态收尾；
- SSE 和来源结构变化必须同步后端 Schema、前端类型和消费者。

## 验证

- 验证新会话、恢复会话、连续追问、无来源、守卫拒绝和流中断；
- 验证旧回答保留、版本切换、最后一轮限制、旧会话兼容和重新生成失败回退；
- 验证问题原位编辑、取消/空白输入、问题与回答版本关联、首轮标题更新、管理面审计记录和编辑失败回退；
- 验证全部范围、父目录递归、单目录、不可筛选/停用目录拒绝、空启用范围无条件检索保护，以及旧分类名称会话的重新生成兼容；
- API/Auth 变化同时验证匿名、普通用户、管理员和 CSRF；
- 管理后台回答策略页只对 `role == 'admin'` 开放，后端 API 仍以 `require_admin` / `require_csrf_admin` 强制鉴权；
- 前端变化运行 `npm run build` 和回答策略页相关单元测试。

## 已知限制

- 当前历史来源携带预算有限，不代表完整跨文档记忆。

## 相关决策

- 暂无独立 ADR；现有约束见根目录 `CLAUDE.md`。

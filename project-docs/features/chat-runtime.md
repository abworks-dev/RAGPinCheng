# 对话运行时

- 状态：已实现
- 最后核对：2026-07-22

## 用户可观察能力

登录用户可以创建、恢复、连续追问和删除对话；回答通过 SSE 流式返回，并在完成后持久化消息、来源和会话检索状态。

## 当前边界

### 已实现

- `ChatSession` 统一编排查询守卫、重写、检索、上下文携带、预算和生成；
- HTTP 请求通过 `api/conversation_runtime.py` 恢复会话、按会话加锁并持久化；
- 同步与流式路径共享生成准备和状态收尾逻辑；
- SSE 返回准备、正文、完成和错误状态，并保持来源 DTO 一致。

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
→ ChatSession.ask_stream
→ retrieve_for_turn
→ stream_generate
→ wrap_stream_with_persistence
→ messages / conversations in app.sqlite
```

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
- `messages.sources_json` 只供 UI 恢复，不重新进入 LLM 上下文。

## 依赖与下游消费者

- 依赖检索、回答生成、认证、app.sqlite；
- 下游包括前端流读取器、消息列表、引用和反馈。

## 不变量与安全边界

- 新入口不得复制或绕过 `ChatSession` 编排；
- HTTP 聊天必须经过 `conversation_runtime.py` 的恢复、锁和持久化；
- 流必须被完整消费或关闭，才能可靠完成状态收尾；
- SSE 和来源结构变化必须同步后端 Schema、前端类型和消费者。

## 验证

- 验证新会话、恢复会话、连续追问、无来源、守卫拒绝和流中断；
- API/Auth 变化同时验证匿名、普通用户、管理员和 CSRF；
- 前端变化运行 `npm run build`。

## 已知限制

- 当前历史来源携带预算有限，不代表完整跨文档记忆。

## 相关决策

- 暂无独立 ADR；现有约束见根目录 `CLAUDE.md`。


# 转录管理流程加固

- 状态：PR 1 代码完成待验证；PR 2 未开始
- 风险：R2（跨前端、API 与 Provider 请求身份契约）
- 基线：多引擎转录 Phase 5A/5B

## 目标

在不改变“候选索引成功并原子切换正式 head 后才算 published”的安全语义下，修复重复媒体转录请求身份冲突、审核状态枚举不匹配和管理列表信息过载，并为后续独立转写工作台提供稳定入口。

本计划不改写已经完成验收的 `multi-engine-transcription-phase5.md` 历史基线。

## PR 1：正确性与列表交互

已冻结并实现：

- `application_job_id = transcription_jobs.id`，由不可变 `ProviderRuntimePorts` 传入 Provider；
- ASR 服务请求身份包含 application job、媒体、音频摘要与尺寸/时长、Provider、Profile、execution fingerprint；
- 同一应用任务的网络重试复用请求身份，同一媒体的新应用重试任务获得新请求身份；
- 安全解析 ASR 服务 `detail.code`，区分服务身份冲突与契约不匹配；
- API 返回安全的 `code/message/retryable` 失败对象，前端只按该策略开放重试；
- 前端审核状态使用真实枚举 `review_approved/review_rejected`；
- 媒体列表只展示唯一当前阶段和独立索引状态，不再在表格行中展开大型版本卡片；
- 提供处理中、待审核、发布处理中、失败快捷筛选；当前仅筛选最近加载的 100 条；
- 发布仍是审核通过后构建候选索引并原子 promote 的单一业务动作，不提供“已发布后再手动索引”。

明确不做：

- 不修改数据库结构，不清理历史任务或媒体；
- 不实现 Markdown 编辑、说话人替换、低置信度筛选、评论、翻译对照、ETA 或移动端审核；
- 不修改不可变历史 Markdown；
- 不运行真实媒体、GPU、Qdrant 或生产回归。

## PR 2：独立转写工作台基础版

后续需重新核对并按 R2 审批，范围限定为：

- 独立页面的视频与时间戳联动；
- 忠实只读 Markdown 渲染预览与历史 token 告警；
- 审核意见、通过、驳回（驳回原因由后端强制必填）；
- 发布进度和版本历史。

不包含可视化 Markdown 编辑、评论、说话人库、低置信度审核和翻译对照。

## 验证与回滚

- Provider/应用/API 契约测试覆盖请求身份、错误映射和真实审核枚举；
- 前端定向测试与 production build；
- 远端 CI 在干净环境执行 Phase 4/5 及完整前端回归；
- 回滚时整体撤销 PR 1；新增 API 字段均为兼容性字段，无数据库迁移或数据回滚。

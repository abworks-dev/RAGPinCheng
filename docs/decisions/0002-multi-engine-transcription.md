# 0002 — 多引擎视频自动转录与管理员选择

- 状态：已批准（架构；Phase 1～5A/5B 已在后续实现中落地，真实引擎资格和生产准入仍由独立 workflow 管理）
- 日期：2026-08-01
- 关联功能：[视频转录链路](../features/transcript-pipeline.md)、[文档摄取与索引](../features/document-indexing.md)、[认证与授权](../features/authentication.md)
- 关联方案：[多引擎视频自动转录总体实施方案](../plans/multi-engine-auto-transcription.md)

## 背景

现有媒体链路保留 MP4 与人工 Markdown 回退路径；自动 ASR、版本、审核、发布和索引能力已在后续 Phase 1～5A/5B 中实现。各引擎的真实资格、运行时身份和应用准入继续独立管理，不能把代码存在或历史候选评测当成当前生产能力。

若继续采用“先选出唯一 ASR 赢家，才建设统一流水线”的方式，一个候选的失败会阻塞与模型无关的 JSON、版本、审核、发布和索引能力，也会把已实现候选误等同于应开放的正式功能。

## 决策

1. 自动转录采用 **Provider + Profile** 架构：Provider 封装引擎，Profile 是管理员可选择的服务端白名单配置。
2. 管理员只能选择 `profile_id`，不能提交任意模型路径、下载来源、热词正文或底层参数。
3. Profile 具有 `pending_evaluation | approved | experimental | unavailable | disabled | deprecated` 资格/开放状态。
4. `experimental` Profile 仅管理员可选，强制人工审核，禁止自动发布和自动索引；只有 `approved` Profile 可以成为系统推荐目标。
5. 所有 Provider 输出统一 Canonical Transcript JSON，再由确定性 formatter 生成现有 transcript Markdown；原始引擎输出不能直接进入索引。
6. 同一媒体允许保留多个成功历史版本，但同时最多一个活跃转录任务，并且同时只能有一个正式发布版本。
7. 转录成功、人工审核、正式发布和索引是四个独立状态边界；只有明确发布的版本可以创建索引任务。
8. 现有 MP4 + 人工 Markdown 路径永久保留，自动转录默认关闭。
9. Phase 1 的统一 Schema、Provider Protocol、Profile Schema 和 formatter 与候选资格评测解耦；某个候选失败只改变该 Profile 状态，不阻塞统一流水线。
10. 单卡 GPU 下 ASR 保持独立服务、串行运行并让在线 BGE 优先；本决策不授权生产执行或多引擎并行加载。

## 备选方案

- **固定 FunASR 为唯一引擎**：实现较简单，但当前实测存在场景性质量缺口，且未来替换会重新改动任务、API 和 UI 契约。
- **等待 faster-whisper 完成并选出唯一赢家**：延迟所有引擎无关能力，并可能在第二个候选也不合格时继续阻塞。
- **把所有实现过的模型直接列为正式选项**：会把沉没开发成本误当作上线资格，增加误选、误发布和运维风险。
- **自动运行多个引擎并投票/合并**：单卡资源和质量归因复杂度过高，当前不采用。

## 影响

- Phase 1～Phase 4 的 Schema、任务、API 和 UI 必须携带 Profile、Provider、模型 revision 和配置身份。
- 需要新增 Profile Registry、Provider Registry、Canonical JSON、转录任务和转录版本契约。
- 管理端将支持选择转录 Profile、查看历史版本、审核、修订和发布。
- 现有人工转录、索引、引用和播放协议必须保持兼容。
- FunASR 和 faster-whisper 的资格评测成为独立候选工作，不再互相替代，也不自动授权生产集成。
- 本 ADR 只记录已批准架构，不代表多引擎转录已经实现或可在生产使用。

## 回滚或替代

- 通过 `ASR_ENABLED=false` 关闭自动转录，继续使用人工 Markdown 流程。
- 可以单独禁用或废弃某个 Profile，不影响其他候选、历史版本和当前正式稿。
- 数据库变更应采用添加式迁移；回滚功能时保留真实转录版本和历史任务，不自动删除数据。
- 后续如采用外部托管 ASR、增加 GPU 或引入组合模型，应新增 ADR 替代或扩展本决策。

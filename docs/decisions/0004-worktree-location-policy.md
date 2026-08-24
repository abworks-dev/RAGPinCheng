# 0004 — Git worktree 创建位置与兼容策略

- 状态：已批准
- 日期：2026-08-17

## 背景

项目历史 worktree 分散在 Codex 用户目录、仓库内部、系统临时目录和主仓库同级目录。Git 注册能够证明仓库归属，但不能提供统一的生命周期和清理边界，导致扫描开销、目录残留和人工清理风险持续增加。

OpenAI 的 Codex App 受管 worktree 使用 `$CODEX_HOME/worktrees`，并负责关联任务、快照和保留数量。人工长期 worktree 不具备这些生命周期能力，需要独立且集中的路径边界。

## 决策

1. Codex App 任务优先使用 `$CODEX_HOME/worktrees/**`。
2. 确需人工创建的长期 worktree 使用主仓库父目录下的 `.worktrees/<仓库名>/**`；本项目默认路径为 `E:\Repository\Github\.worktrees\RAGPinCheng\**`。
3. 新任务不得使用仓库内部 `.codex-worktrees/**`、系统临时目录或 `RAGPinCheng-*` 同级散列目录。
4. 所有写任务仍须是同一仓库正式注册的非主 worktree，并遵守分支与 workspace 预检门禁。
5. 已有旧位置不强制中途迁移，只允许原任务以 `Intent Continue` 继续；任务结束后按已批准的 R3 清理流程处理。
6. 人工长期 worktree 的生命周期统一通过 `scripts/Register-CodexWorktree.ps1`、`scripts/Close-CodexWorktree.ps1` 和 `scripts/Audit-CodexWorktrees.ps1` 管理；创建前必须通过 workspace 写预检，关闭默认拒绝 dirty worktree，审计结果作为清理前的只读依据。
7. 生命周期 metadata 存放在 Git common directory 下的 `codex-worktrees/`，按规范化路径哈希命名，不写入 worktree 根目录，避免污染任务分支。

## 影响

- 新任务获得稳定、可识别的路径边界，便于自动巡检和保守清理。
- Codex App 保持官方生命周期管理，不由项目脚本复制或模拟。
- 人工 worktree 具备可追踪的注册、关闭和审计记录；缺失 metadata 的 worktree 不得被自动清理。
- 旧任务不会因规则上线立即中断，但不能在旧位置开启新任务。
- 本决策不迁移或删除现有 worktree，也不改变远端分支和 CI 行为。

## 回滚

回退协作规则、workspace 门禁和测试提交即可恢复为只校验 Git 注册关系；现有 worktree无需随规则回滚而移动。

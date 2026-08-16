# 项目文档

本目录只存放可跟踪的项目开发与运维文档，不是业务资料摄取入口。业务资料由受管 `content/` 存储维护；旧文件系统兼容入口默认为 `content/legacy-docs/`。

## 导航

- `features/`：当前源码和可复核运行结果能够证明的功能事实；
- `decisions/`：已批准并长期生效的架构决策；
- `plans/`：候选方案、实施方案和历史执行边界；
- `migrations/`：环境及数据迁移手册；
- `operations/`：部署、同步、排障和运维速查；
- `design/`：界面和交互设计契约；
- `design/page-inventory.md`：页面、tab、权限、API、状态和验证入口清单；
- `USER_ACCEPTANCE.md`：用户验收格式与要求。

新增或修改功能时，从 `features/README.md` 进入，并以源码、Schema、配置和实际验证结果为最终依据。

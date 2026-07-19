# Security, Data and Deployment Rules

- 不读取或输出`.env`真实值；只讨论变量名和是否存在。
- 不提交`docs/`、`data/*.sqlite*`、Qdrant存储、反馈日志、Cookie或客户文件。
- `.claude/settings.local.json`是本机私有配置，不进入版本控制。
- MinerU和GLM是外部服务；发送真实资料前确认数据授权和保密边界。
- 认证改动必须验证匿名、普通用户、管理员、Cookie Secure和CSRF。
- Qdrant运行时不得直接操作底层Volume。
- 两个SQLite必须分开备份、迁移和恢复。
- Docker命令从仓库根目录使用`-f docker/docker-compose.yml`。
- GPU可用需同时满足cu128 Torch、Compose GPU reservation和主机驱动/Container Toolkit；不要只看主机显卡。
- Reset、删除、`down -v`、生产部署、密钥轮换和用户数据操作必须先确认环境、备份与回滚。
# 认证与授权

- 状态：已实现
- 最后核对：2026-07-22

## 用户可观察能力

用户可以注册、登录、注销和恢复登录状态；普通用户访问自己的会话，管理员访问用户、索引和反馈管理功能。

## 当前边界

### 已实现

- 密码哈希与校验；
- 服务端 Session、HttpOnly Cookie 和 CSRF Token；
- `require_user`、`require_admin`、`require_csrf`、`require_csrf_admin` 依赖；
- 会话到期、注销和管理员环境引导。

### 未实现

- 企业 SSO、OIDC 或细粒度资料 ACL；
- 面向单个视频或文档的独立授权模型。

## 入口与调用链

```text
/api/register | /api/login
→ password verify/hash
→ issue_session(app.sqlite)
→ session Cookie + CSRF token
→ require_user / require_csrf
→ chat/admin routes
```

## 关键文件

- `api/auth.py`
- `api/routes_auth.py`
- `api/db.py`
- `api/routes_chat.py`
- `api/routes_admin.py`
- `frontend/src/api/client.ts`

## 数据契约

- app.sqlite 中的用户与会话状态；
- Session Cookie 标识服务端会话；
- 修改类请求同时校验 Cookie 身份与 CSRF Header；
- `CurrentUser` 是路由层授权上下文。

## 依赖与下游消费者

- 依赖 app.sqlite、Cookie 配置和前端 API 客户端；
- 下游为聊天、会话、反馈和全部管理员接口。

## 不变量与安全边界

- 前端隐藏按钮不是权限控制；
- 认证必须由后端依赖强制；
- 不读取、输出或提交真实密码、Cookie、密钥和用户对话；
- app.sqlite 不得与可重建的 parents.sqlite 混同或随索引 Reset 删除。

## 验证

- 验证匿名、普通用户、管理员和跨用户资源访问；
- 验证登录、注销、过期 Session、错误密码和 CSRF 缺失/错误；
- 检查 Cookie Secure 等部署配置。

## 已知限制

- 当前授权主要按用户和管理员角色划分，不代表已经支持资料级 ACL。

## 相关决策

- 暂无独立 ADR。


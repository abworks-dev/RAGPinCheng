# 前端手工回归清单

> 本清单用于 P0 基线和后续视觉改造回归。执行前请记录 commit、后端版本、浏览器和测试账号类型。

## 自动检查

- [ ] `npm run test:run`
- [ ] `npm run build`
- [ ] `npm run test:visual -- admin-workflows.spec.ts`
- [ ] T4 合入并接受 golden 后执行 `npm run test:visual -- admin-golden.spec.ts`
- [ ] 视觉测试连续运行两次且第二次无截图 diff
- [ ] TypeScript 无诊断
- [ ] `chatStream` 的 `prep/token/done/error`、跨 chunk、AbortSignal 测试通过
- [ ] API client 的 Cookie、CSRF、401 和管理 API 测试通过
- [ ] 引用解析的数字、PDF、视频、越界测试通过
- [ ] 登录页提交、错误和注册链接测试通过

## 需要普通用户测试账号

- [ ] 登录、登出、会话恢复
- [ ] 问答空状态
- [ ] 提交问题并观察 SSE 的准备、生成、结束和错误状态
- [ ] 切换会话时旧请求被取消
- [ ] 历史消息恢复和会话持久化
- [ ] 引用脚标、hover、点击、来源定位和复制
- [ ] PDF、DOCX、XLSX、PPTX 预览及错误状态
- [ ] 视频播放器打开、鉴权、播放、时间戳 seek 和错误状态
- [ ] 移动端抽屉、输入框和横向溢出

## 需要管理员测试账号和合成测试数据

- [ ] `/admin` 权限拦截和管理员页面加载
- [ ] 用户启停、角色切换、密码重置
- [ ] 对话查看和反馈查看
- [ ] 文件分类、多文件上传和上传失败
- [ ] 资料管理中的索引任务轮询、筛选、历史尝试和失败重试
- [ ] 资料删除及确认流程
- [ ] 视频媒体上传和媒体列表
- [ ] 过期对话清理

## 隔离的管理后台浏览器回归

- [ ] Chromium viewport 覆盖 1440x900、1280x720、768x1024、390x844
- [ ] 资料工作流覆盖 normal、loading、empty、error、disabled、busy
- [ ] 分类设置覆盖 normal、loading、empty、error、disabled、busy
- [ ] `body` 和页面根节点无横向溢出，宽表仅在自身容器滚动
- [ ] 移动端导航、对象身份和核心操作可发现、可聚焦且位于 viewport 内
- [ ] 导航、标题、表单、表格或移动列表无互相遮挡
- [ ] fixture 未声明的 `/api/**` 请求会失败，未访问真实后端
- [ ] 失败截图、trace 和 HTML report 不含真实身份或业务资料

## 当前不安全或无法自动执行的项目

- [ ] 不使用真实客户资料、真实密码、生产索引任务或未经授权的外部服务
- [ ] 未提供安全测试账号时，不执行管理员、上传、媒体和索引人工回归
- [ ] T4 最终页面尚未合入并人工接受 golden 前，不启用像素比较 required CI

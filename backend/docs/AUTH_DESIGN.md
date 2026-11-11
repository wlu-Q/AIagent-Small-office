# 本地单用户身份与可选认证设计

本文档描述小小办公室裁剪后的身份边界。默认形态是本机、单进程、单操作者；浏览器和 Gateway 不需要登录即可使用。完整账号认证仍保留为显式可选能力，但不参与默认冷启动。

## 默认模式

未设置环境变量时，`DEER_FLOW_AUTH_DISABLED` 默认视为 `1`。

`AuthMiddleware` 为非公开请求注入固定的本地管理员身份：

- `id=default`
- `email=default@test.local`
- `system_role=admin`
- `request.state.user`、`request.state.auth` 和运行时 `ContextVar` 使用同一个身份

因此路由、Repository、线程目录、Memory 和 Agent 配置仍然经过统一的身份解析，而不是在各层分别写“无认证”特判。默认模式下 CSRF 校验关闭，因为不存在基于 Cookie 的登录会话。

## 显式启用认证

需要多用户或浏览器登录时，操作者必须明确设置 `DEER_FLOW_AUTH_DISABLED=0`。

此时非公开路由必须携带有效 `access_token` Cookie。认证成功后，Gateway 将真实用户写入 `request.state.user`、`request.state.auth` 和运行时 `ContextVar`；状态修改请求同时执行双提交 CSRF 校验。

以下路径保持公开，用于健康检查、API 文档和登录初始化：

- `/health`
- `/docs`、`/redoc`、`/openapi.json`
- `/api/v1/auth/login/local`
- `/api/v1/auth/register`
- `/api/v1/auth/setup-status`
- `/api/v1/auth/initialize`
- `/api/v1/auth/providers`

生产环境不会接受认证绕过。`DEER_FLOW_ENV=production` 或 `ENVIRONMENT=production` 时，即使设置 `DEER_FLOW_AUTH_DISABLED=1`，Gateway 仍强制认证。

## 数据隔离

Repository 的 `user_id` 参数遵循三种明确语义：

- `AUTO`：从当前运行时身份解析用户；没有身份时立即报错。
- 字符串 ID：由可信服务端调用显式指定用户。
- `None`：跳过用户过滤，只允许迁移或受控管理脚本使用。

默认本地模式使用固定 `default` 用户，因此单用户数据仍保留稳定的所有权字段和目录边界。切换到认证模式后，同一套 Repository 和路径解析自动按真实用户隔离，不需要更换运行时主链。

## 安全边界

- 客户端 metadata 不能覆盖服务端注入的 `user_id`、`user_role` 或 OAuth 归属。
- 本地单用户模式只适用于受信任的本机操作者，不是远程多租户安全边界。
- `LocalSandboxProvider` 默认禁用宿主机 Bash；启用高风险工具仍应经过独立权限控制。
- 已删除的外部消息渠道、内部令牌 Header 和 Webhook 身份不属于当前身份模型，不得重新作为隐藏认证入口接回主链。

## 启动检查

默认启动不应要求账号、外部身份服务或额外凭据。启用认证后，至少验证：

1. 无 Cookie 的非公开请求返回 401。
2. 无效或过期 Cookie 返回 401。
3. 登录用户只能读取自己的线程、运行、Memory 和 Agent 配置。
4. 状态修改请求缺少或伪造 CSRF Token 时返回 403。
5. 生产环境无法通过环境变量开启认证绕过。

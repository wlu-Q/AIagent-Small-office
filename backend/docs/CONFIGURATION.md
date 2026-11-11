# 当前配置

仓库只有两个运行配置入口：

- `config.yaml`：模型、Runtime、存储、安全和调度；
- `extensions_config.json`：MCP Server、MCP Interceptor 和 Skill 开关。

模板分别是 `config.example.yaml` 与 `extensions_config.example.json`。

## 1. 路径

默认从项目根目录读取配置。可用环境变量：

- `DEER_FLOW_PROJECT_ROOT`
- `DEER_FLOW_CONFIG_PATH`
- `DEER_FLOW_HOME`

本地 `config.yaml`、`extensions_config.json`、`.deer-flow/` 和真实密钥不提交 Git。

## 2. `config.yaml` 顶层

| 节 | 作用 |
| --- | --- |
| `config_version` | 当前配置结构版本 |
| `log_level`, `logging` | 日志级别、格式与目标 |
| `token_usage`, `token_budget` | Token 统计和预算 |
| `max_recursion_limit` | 图执行上限 |
| `models` | 模型定义与默认模型 |
| `tool_groups`, `tools` | 工具声明与权限元数据 |
| `tool_search` | 延迟工具发现 |
| `tool_output` | 工具输出预算与裁剪 |
| `suggestions` | 建议生成配置 |
| `input_polish` | 输入润色模型和长度 |
| `loop_detection` | 重复工具调用检测 |
| `read_before_write` | 写文件前读取约束 |
| `safety_finish_reason` | 模型安全终止处理 |
| `uploads` | 上传大小、扩展名和转换设置 |
| `sandbox` | Sandbox Provider、挂载和执行配置 |
| `skills` | Skill 存储与容器路径 |
| `skill_scan` | Skill 静态安全扫描 |
| `title` | Run/线程标题生成 |
| `summarization` | 上下文摘要和持久 Skill 引用 |
| `memory` | Memory 后端与注入 |
| `skill_evolution` | Skill 演化开关 |
| `database` | SQL 后端与连接 |
| `run_events` | RunEventStore 后端 |
| `scheduler` | 定时任务调度 |
| `stream_bridge` | 进程内或 Redis 流桥 |
| `authorization` | 权限策略 |

字段、默认值和注释以 `config.example.yaml` 为准。

## 3. 模型

模型配置包含 Provider、模型名、凭据环境变量、上下文长度和能力标记。Office Run 通过服务端模型绑定选择有效配置；外部请求不能传入未配置模型绕过允许列表。

建议：

- 密钥使用 `${ENV_NAME}`；
- 为不同用途配置明确名称；
- 修改模型后重新验证真实 Run；
- 不把“模型出现在列表中”当作 Provider 调用成功。

## 4. Database、Checkpoint 与 Store

Checkpointer 与 LangGraph Store 使用 `database` 选择的当前后端，不存在独立配置文件。

- `memory`：仅进程内；
- `sqlite`：本地持久化；
- 其他 SQL 后端：按 Provider 配置。

schema migration 必须随数据库结构执行。删除本地数据库会删除本地持久化事实，不是升级手段。

## 5. Run Events 与 Stream Bridge

`run_events` 决定 Run 事件存储。`stream_bridge` 决定运行事件如何在进程内或 Redis 中传递。它们必须与当前部署方式匹配。

默认本地运行不要求 Redis。

## 6. Sandbox

默认 Provider 是 LocalSandbox。远程或容器 Provider 需要额外依赖、凭据和生命周期验证。

所有 Provider 都必须遵守：

- 用户隔离；
- 线程目录；
- workspace/uploads/outputs 虚拟路径；
- 只读挂载；
- 路径穿越与符号链接防护。

## 7. Skill 与 MCP

Skill 路径和存储在 `config.yaml.skills`。MCP Server 与 Skill 启用状态写入根目录 `extensions_config.json`。

`extensions_config.json` 的 `mcpServers` 每项可包含：

- `enabled`
- `type`
- `command` / `args` / `env`
- `url` 或 Provider 所需连接字段
- `tool_call_timeout`
- `routing`
- `tools`

修改后通过 Gateway API 或进程重载刷新缓存。

## 8. 运行上下文

请求级业务参数统一进入：

```python
config["context"]
```

`config["configurable"]` 只保留：

- `thread_id`
- `checkpoint_id`
- `checkpoint_ns`
- `checkpoint_map`

不得把用户、员工、模型、项目或 Office 权限重新放入 `configurable`。

## 9. 安全

- 不提交 `config.yaml`、`extensions_config.json`、`.env` 或 `.deer-flow/`。
- 不在日志、事件、Run 启动合同或错误消息中暴露 Secret。
- 不关闭认证、CSRF、路径校验、Skill 扫描或 Office 权限来减少代码。
- 远程 Provider、OIDC、Redis 和外部数据库必须分别验证连接与权限。
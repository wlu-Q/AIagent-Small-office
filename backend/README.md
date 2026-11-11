# 小小办公室 DeerFlow Backend

`backend/` 是小小办公室唯一 Agent 后端。FastAPI Gateway 在进程内组合 Office 领域服务、DeerFlow Agent、RunManager、Checkpointer、Run/Event Store、Sandbox、Skill、MCP 和 Memory。

## 当前请求链

```text
Office HTTP request
  -> app.gateway.routers.office
  -> deerflow.office.message_pipeline.OfficeMessagePipeline
  -> deerflow.office.run_launch.OfficeRunLauncher
  -> app.gateway.services.start_run
  -> deerflow.runtime.runs.RunManager
  -> DeerFlow worker / lead agent
```

查询端通过 `OfficeRunView` 读取 DeerFlow Run/Event/Thread 事实；生命周期投影通过
`OfficeRunProjection` 更新员工状态和群聊最终回复。后端不存在 Office 自有 RunStore、
Checkpoint、Event Bus 或 Agent Loop。

业务运行参数位于 `config["context"]`；`config["configurable"]` 只保存 Checkpoint 身份字段。

## 当前 API

- `/api/office/*`：项目、员工、能力路由、Run、事件和工件。
- `/api/models*`：模型配置读取。
- `/api/skills*`：Skill 查询、安装和自定义 Skill 管理。
- `/api/mcp/*`：MCP 配置与缓存刷新。
- `/api/memory*`：Memory 查询、导入导出和事实管理。
- `/api/scheduled-tasks*`：定时任务。
- `/api/input-polish`：输入润色。
- `/api/v1/auth/*`：本地认证与 OIDC。
- `/health`：健康检查。

完整路由见 `docs/API.md` 或启用后的 `/docs`。

## 核心模块

| 目录 | 职责 |
| --- | --- |
| `app/gateway/` | FastAPI、认证、CSRF、依赖注入、服务启动 |
| `packages/harness/deerflow/office/` | 员工、项目、能力、路由、权限、Run 与工件 |
| `packages/harness/deerflow/runtime/` | Run、事件、Checkpoint、Store、Journal |
| `packages/harness/deerflow/agents/` | Lead Agent、状态与 Middleware |
| `packages/harness/deerflow/tools/` | 内建工具与工具装配 |
| `packages/harness/deerflow/skills/` | Skill 发现、存储与加载 |
| `packages/harness/deerflow/sandbox/` | Sandbox、虚拟路径与挂载 |
| `packages/harness/deerflow/subagents/` | DeerFlow 原生委派执行 |
| `packages/harness/deerflow/persistence/` | 数据库模型与 schema migration |
| `tests/` | 单元、集成、阻塞 I/O 与边界测试 |

## 安装

要求 Python 3.12+ 与 `uv`。

```powershell
Set-Location backend
uv sync
```

项目配置位于仓库根目录：

```powershell
Copy-Item ..\config.example.yaml ..\config.yaml
Copy-Item ..\extensions_config.example.json ..\extensions_config.json
```

密钥优先使用环境变量，不提交真实凭据。

## 启动

从仓库根目录：

```powershell
npm run deerflow:gateway
```

或在 `backend/`：

```powershell
uv run --no-editable uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
```

## 验证

```powershell
uv run pytest
```

按模块验证示例：

```powershell
uv run pytest tests/test_gateway_services.py tests/test_run_manager.py
```

编译检查：

```powershell
uv run python -m compileall -q app/gateway packages/harness/deerflow
```

测试通过不等于真实模型、外部 MCP、容器 Sandbox 或桌面交互已经运行验证。

## 配置与文档

- `docs/SETUP.md`：启动步骤。
- `docs/CONFIGURATION.md`：当前配置入口。
- `docs/API.md`：当前 Gateway 路由。
- `docs/ARCHITECTURE.md`：后端模块与运行链。
- `UPSTREAM.md`：DeerFlow 来源和同步规则。

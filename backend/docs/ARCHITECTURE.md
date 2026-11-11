# Backend 当前架构

## 1. 进程结构

Gateway 是唯一后端进程入口：

```text
uvicorn app.gateway.app:app
  -> FastAPI lifespan
  -> Database / Checkpointer / Store / RunEventStore / StreamBridge
  -> RunManager
  -> Office Router 与其他产品 Router
```

Agent 执行不经过额外 HTTP 代理。Office 服务直接调用进程内 `start_run` 和 `RunManager`。

## 2. Gateway 层

`app/gateway/app.py` 负责：

- 配置与日志初始化；
- 认证、CSRF、CORS、Trace Middleware；
- 数据库、Memory、Scheduler 和运行组件生命周期；
- Router 注册；
- `/health`。

`app/gateway/deps.py` 只暴露当前应用状态中的 RunManager、Checkpointer、Store 等依赖。

`app/gateway/services.py` 是所有 Run 启动路径的共享入口，负责输入规范化、上下文合并、Checkpoint 配置、模型绑定、用户隔离和启动合同持久化。

## 3. Office 层

`packages/harness/deerflow/office/`：

| 模块 | 职责 |
| --- | --- |
| `projects.py` | 项目绑定、项目上下文、文件读取与搜索 |
| `employees.py` | 员工配置、启停、Skill、工具和运行投影 |
| `capability_registry.py` | 用户隔离的员工能力索引 |
| `routing.py` | 请求约束、候选员工和能力缺口 |
| `authority.py` | 委派权限、禁用权限与工具过滤 |
| `messages.py` | 消息命令、Mention 校验和确定性路由 |
| `message_pipeline.py` | 消息 single-flight、员工 FIFO 和 Resume 投递 |
| `run_gate.py` | 每名员工独立的运行准入与排队 |
| `run_launch.py` | 员工运行合同到 DeerFlow Run 的薄转换 |
| `run_view.py` | 员工边界内的 Run、Event 和 Checkpoint 历史查询 |
| `run_projection.py` | 员工状态与群聊最终回复投影 |
| `group_messages.py` | 用户隔离的公共群聊 SQLite 事实 |
| `packages.py` | 员工运行装配数据 |

Router 不重复这些规则。

## 4. Agent 组装

Run executor 根据服务端上下文创建 DeerFlow Agent：

- 解析模型；
- 装配内建工具、配置工具、MCP 和 Skill；
- 应用 Office 权限；
- 建立 Sandbox；
- 注入 Memory、项目和员工上下文；
- 注册 Middleware；
- 使用 LangGraph Checkpointer 执行。

业务参数位于 `config.context`。Checkpoint 身份位于 `config.configurable`。

## 5. Run 与事件

`RunManager` 负责：

- 同线程并发策略；
- Run 状态机；
- 后台任务生命周期；
- 取消、超时、恢复与关闭排空；
- RunStore 持久化。

`RunJournal` 将模型、工具、消息、Middleware 和运行信息写入 `RunEventStore`。当前 AI 消息事件类型是 `llm.ai.response`。

`OfficeRunView` 只查询员工归属下的 DeerFlow Run、Event 与 Thread 历史；
`OfficeRunProjection` 只把 DeerFlow 生命周期投影到员工展示字段和群聊最终回复。
两者都不拥有 Run 真相。

## 6. 持久化

- `database`：用户、员工关联、定时任务、Run、事件等 SQL 数据。
- `checkpointer`：LangGraph 图状态，后端选择跟随 `database`。
- `store`：LangGraph Store，后端选择跟随 `database`。
- `run_events`：Run 事件后端。
- `stream_bridge`：进程内或 Redis 流桥。
- `.deer-flow/users/<user>/...`：用户隔离的文件型配置、Skill、Memory 与线程目录。

数据库 schema migration 是当前数据结构的组成部分，不是应用层旧实现读取旁路。

## 7. Sandbox 与路径

`Paths` 统一建立用户、线程、workspace、uploads、outputs 和 Skill 路径。Sandbox 工具只接受虚拟路径并在服务端解析到允许目录。

LocalSandbox 直接挂载主机目录。远程 Sandbox 在释放时把 workspace 和 outputs 同步回主机；客户端按具体 Run Event 展示工具和制品过程，不存在 Office 跨 Run 工件聚合层。

## 8. 安全边界

- 认证用户由 Gateway 建立并写入运行上下文。
- Office 项目 ID 与规范化路径必须匹配。
- 员工权限在工具进入 Agent 前过滤。
- 外部上下文不能覆盖服务端保留字段。
- Secret 在日志、事件和持久化启动合同中脱敏。
- 文件操作必须通过用户隔离和路径边界校验。
- 密码哈希升级、安全回退和数据库 schema migration 属于安全与数据完整性能力。

## 9. 桌面边界

Electron 通过静态白名单 IPC 访问健康检查、项目绑定、模型列表，以及 Office 员工、消息、队列和 Run 查询/取消/恢复能力。Memory、MCP、Skill、Sandbox 和通用 HTTP 调用不通过 preload 暴露。

## 10. 验证边界

- `compileall`：只证明 Python 文件可编译。
- pytest：只证明被执行测试覆盖的行为。
- `node --test desktop/client-boundary.test.cjs`：证明静态桌面合同与相对导入边界。
- `npm run build`：证明客户端可构建。
- 真实 Gateway、模型、Sandbox、MCP 和 Electron 可见交互需要分别运行验证。

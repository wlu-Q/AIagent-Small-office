# Backend 文档索引

本目录只维护能映射到当前代码、配置或可执行验证的文档。历史实现记录、临时评审、已删除接口和阶段快照不在这里保留。

## 入门与架构

| 文档 | 说明 |
| --- | --- |
| [SETUP.md](SETUP.md) | Gateway 安装与启动 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 当前后端模块和调用链 |
| [CONFIGURATION.md](CONFIGURATION.md) | `config.yaml` 与 `extensions_config.json` |
| [API.md](API.md) | 当前 FastAPI 路由 |
| [AUTH_DESIGN.md](AUTH_DESIGN.md) | 认证、CSRF 与用户隔离 |
| [SSO.md](SSO.md) | OIDC 配置与回调 |

## Agent 与运行安全

| 文档 | 说明 |
| --- | --- |
| [GUARDRAILS.md](GUARDRAILS.md) | Agent Guardrail |
| [middleware-execution-flow.md](middleware-execution-flow.md) | Middleware 执行顺序 |
| [BLOCKING_IO_DETECTION.md](BLOCKING_IO_DETECTION.md) | 阻塞 I/O 检测 |
| [summarization.md](summarization.md) | 上下文摘要 |
| [plan_mode_usage.md](plan_mode_usage.md) | Plan Mode |

## 工具与扩展

| 文档 | 说明 |
| --- | --- |
| [MCP_SERVER.md](MCP_SERVER.md) | MCP Server 集成 |
| [APPLE_CONTAINER.md](APPLE_CONTAINER.md) | 可选 Apple Container Sandbox |
| [AUTO_TITLE_GENERATION.md](AUTO_TITLE_GENERATION.md) | 自动标题 |

## 维护规则

- 文档必须指向当前文件、路由或配置。
- 已删除代码的说明随代码一起删除。
- 测试次数、临时风险和阶段进度只出现在任务报告或 Git 历史中。
- 删除或重命名文档后必须执行引用扫描。
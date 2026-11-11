# Gateway API

当前 FastAPI Gateway 的产品路由如下。请求体和响应模型以运行中的 `/openapi.json` 为准；启用文档时可访问 `/docs`。

## 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | Gateway 健康状态 |

## Office

### 项目

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/office/project` | 当前项目摘要 |
| PUT | `/api/office/project` | 绑定项目目录 |
| GET | `/api/office/project/context` | 当前项目运行上下文 |
| POST | `/api/office/project/files/read` | 读取绑定项目内文件 |
| GET | `/api/office/project/search` | 搜索绑定项目 |

### 员工

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/office/employee-templates` | 可创建的固定岗位模板 |
| GET | `/api/office/employees` | 员工列表 |
| POST | `/api/office/employees` | 创建员工 |
| GET | `/api/office/employees/{employee_id}` | 员工详情 |
| DELETE | `/api/office/employees/{employee_id}` | 删除员工 |
| POST | `/api/office/employees/{employee_id}/enable` | 启用员工 |
| POST | `/api/office/employees/{employee_id}/disable` | 禁用员工 |
| PUT | `/api/office/employees/{employee_id}/personal-info` | 保存个人信息 |
| GET | `/api/office/employees/{employee_id}/capabilities` | 当前能力画像 |

### 消息与队列

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/office/messages` | 发送群聊或私聊消息 |
| GET | `/api/office/group/messages` | 读取公共群聊消息 |
| GET | `/api/office/employees/{employee_id}/conversations/{kind}/messages` | 读取员工 Thread 可见历史 |
| GET | `/api/office/queue` | 查询员工 FIFO |
| PATCH | `/api/office/queue/{queue_id}` | 编辑未启动消息文本 |
| DELETE | `/api/office/queue/{queue_id}` | 删除未启动消息 |

### Run 与事件

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/office/employees/{employee_id}/runs` | Run 列表 |
| GET | `/api/office/employees/{employee_id}/runs/{run_id}` | Run 详情 |
| GET | `/api/office/employees/{employee_id}/runs/{run_id}/events` | Run 事件 |
| POST | `/api/office/employees/{employee_id}/runs/{run_id}/cancel` | 取消 Run |
| POST | `/api/office/employees/{employee_id}/runs/{run_id}/resume` | 用结构化用户回答恢复中断 Run |

## 模型

| 方法 | 路径 |
| --- | --- |
| GET | `/api/models` |
| GET | `/api/models/{model_name}` |

## Skill

| 方法 | 路径 |
| --- | --- |
| GET | `/api/skills` |
| POST | `/api/skills/install` |
| GET | `/api/skills/{skill_name}` |
| PUT | `/api/skills/{skill_name}` |
| GET | `/api/skills/custom` |
| GET | `/api/skills/custom/{skill_name}` |
| PUT | `/api/skills/custom/{skill_name}` |
| DELETE | `/api/skills/custom/{skill_name}` |
| GET | `/api/skills/custom/{skill_name}/history` |
| POST | `/api/skills/custom/{skill_name}/rollback` |

## MCP

| 方法 | 路径 |
| --- | --- |
| GET | `/api/mcp/config` |
| PUT | `/api/mcp/config` |
| POST | `/api/mcp/cache/reset` |

MCP 配置文件是仓库根目录 `extensions_config.json`。

## Memory

| 方法 | 路径 |
| --- | --- |
| GET | `/api/memory` |
| DELETE | `/api/memory` |
| POST | `/api/memory/reload` |
| GET | `/api/memory/config` |
| GET | `/api/memory/status` |
| GET | `/api/memory/export` |
| POST | `/api/memory/import` |
| POST | `/api/memory/facts` |
| PATCH | `/api/memory/facts/{fact_id}` |
| DELETE | `/api/memory/facts/{fact_id}` |

## 定时任务

| 方法 | 路径 |
| --- | --- |
| GET | `/api/scheduled-tasks` |
| POST | `/api/scheduled-tasks` |
| GET | `/api/scheduled-tasks/{task_id}` |
| PATCH | `/api/scheduled-tasks/{task_id}` |
| DELETE | `/api/scheduled-tasks/{task_id}` |
| POST | `/api/scheduled-tasks/{task_id}/pause` |
| POST | `/api/scheduled-tasks/{task_id}/resume` |
| POST | `/api/scheduled-tasks/{task_id}/trigger` |
| GET | `/api/scheduled-tasks/{task_id}/runs` |

## 输入润色

| 方法 | 路径 |
| --- | --- |
| POST | `/api/input-polish` |

该接口只返回改写文本，不创建 Run，也不持久化消息。

## 认证

统一前缀：`/api/v1/auth`。

| 方法 | 路径 |
| --- | --- |
| GET | `/setup-status` |
| POST | `/initialize` |
| POST | `/login/local` |
| POST | `/register` |
| POST | `/logout` |
| POST | `/change-password` |
| GET | `/me` |
| GET | `/providers` |
| GET | `/oauth/{provider}` |
| GET | `/callback/{provider}` |

完整路径需要加上 `/api/v1/auth`。

## 运行上下文

Office Run 的业务字段进入 `config.context`。`config.configurable` 只保留 `thread_id`、`checkpoint_id`、`checkpoint_ns`、`checkpoint_map`。

认证用户、员工身份、Office 权限、项目绑定和服务端模型绑定由 Gateway 写入，不能由外部请求冒充。

## 错误与权限

- 参数错误通常返回 400 或 422。
- 未认证返回 401。
- 权限不足返回 403。
- 资源不存在返回 404。
- 冲突返回 409。
- 模型或外部服务不可用可能返回 503。

具体状态码以对应 Router 和 OpenAPI 为准。

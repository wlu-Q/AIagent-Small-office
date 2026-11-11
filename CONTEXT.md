# 小小办公室领域词汇

本文件是仓库唯一领域词汇表。代码、文档和任务报告使用这里的名称。

## 1. 产品层

### 小小办公室

整个产品和仓库。包含 Office 后端、Agent 运行层、React/Phaser 客户端和 Electron 桌面壳。

### Office

小小办公室的业务领域层，代码位于 `deerflow.office`。它拥有员工、项目、消息路由、员工 FIFO、能力路由、权限和 Run 展示投影。

### 办公室资料库（Office Library）

用户主动上传资料的独立 RAG 模块，核心位于 `backend/office_library/`。拥有原文件、切片、向量索引及来源版本；通过 `search_office_library` 为员工提供参考，不自动转成 Memory、Project Knowledge 或执行权限。数据按用户隔离、同用户跨项目共享。

### Gateway

`app.gateway.app` 创建的 FastAPI 应用。它是唯一后端进程入口和 HTTP 边界。

### Client

`client/` 中的 React + Phaser 渲染应用。当前展示办公室画布、员工管理、群聊/私聊、队列、Run 过程、Gateway 状态、项目绑定、缩放和 Toast。

### Desktop

`desktop/` 中的 Electron 主进程与 preload。负责窗口、Gateway 生命周期、目录选择和受限 IPC。

### DesktopBridge

`window.officeDesktopBridge`。它只暴露经过白名单约束的 Gateway 状态、项目、员工、消息、队列、Run 查询/取消/恢复和缩放方法，不是通用 HTTP 代理。

## 2. 人员与权限

### Employee

持久化员工配置及其运行投影。员工拥有 Prompt、模型绑定、Skill、工具授权、能力画像和 Office 权限。

### Global Default Model

办公室 `office-bootstrap.yaml` 中的 `default_model_profile_id`。新建员工时将当前默认模型复制到员工 `config.model`，之后切换全局默认不改变已有员工的绑定。员工空闲时可通过个人配置单独更换模型；启动时将具体模型 ID 固化到 Run 合同，恢复不会重新选择模型。模型注册信息与连接配置保存在 Agent 运行层 `config.yaml`，由 `OfficeModelService` 管理添加与默认切换。

### PM

Office 中负责理解群聊需求和协调多员工执行的 Employee。PM 使用 Agent 运行层原生 Agent loop，按权限组合工具、独立 Skills 和真实 Employee；Agent 装配和原生 `task` 准入不再依赖 Workflow 阶段或阶段回复协议。原生最终回答结束 Run；有效 ask 使用 Checkpoint 恢复并保持 PM 忙碌，生命周期不再推进业务阶段。Office 不创建平行的执行循环或调度器。

### Regular Employee

没有 Delegation Permission 的 Employee。它只执行用户直接发送或 PM 通过 Agent 运行层原生 `task` 委派给自己的工作，不能调用 `task` 或继续召集其他 Employee。

### Authority

员工本次运行可用权限的持久化事实。权限只描述能否委派、禁用工具组、禁用权限和精确工具白名单，不表达岗位身份。

### Delegation Permission

调用 Agent 运行层原生 `task` 工具召集 Employee Subagent 的权限。只有 PM 拥有该权限；Regular Employee 的工具集合中不包含 `task`。

### Employee Projection

供 Office 展示的员工运行状态。状态只有：

- `idle`
- `working`
- `disabled`

投影不是 Run 真相；Run 真相由 RunManager 和 RunStore 保存。

### Personal Info

员工自己的持久化信息，由 Office Employee API 管理，包含可在 idle 时修改的姓名、Prompt、个人 Skills 和模型绑定；头像、工具、权限和委派能力不属于可编辑个人信息。它不等于全局用户 Memory。

## 3. 能力与路由

### Capability Profile

由员工当前配置、Skill、工具和运行合同生成的能力描述。

### Capability Index

用户范围内所有当前员工能力的索引，由 `EmployeeCapabilityRegistry` 维护。

### Routing Request

路由请求中的明确目标、能力、工具、Skill、权限和约束。

### Routing Resolution

`search_employee_registry` 查询当前可用候选和忙碌人员。PM 自主选择，`task` 按当前工具合同及员工执行位接单；不存在团队级 ready 审批，忙碌的 PM 委派不排队，用户直接消息继续使用 FIFO。

### Effective Runtime Contract

员工本次 Run 实际使用的模型、Skill、工具、权限和能力集合。

## 4. 项目与文件

### Project Binding

当前用户绑定的一个规范化项目目录及其稳定 `projectId`。

### Project Context

由 `OfficeProjectService` 生成并注入 Run 的项目 ID、路径、摘要和任务上下文。

### Workspace

线程 Sandbox 中的工作目录。Office 写入使用 Execution Workspace；Project Binding 始终指向用户原项目，不随任务目录改变。

### Apply Patch

员工的文本编辑工具 `apply_patch(patch, target?)`，按补丁上下文新建、修改、删除或移动 UTF-8 文件。target 省略时使用任务默认目录，`project` 指向绑定目录，真实工作区 ID 指向同用户、同项目的已有工作区；仅影响本次操作，不修改任务默认环境。沿用 Authority、路径与 Secret 安全；Office 不要求历史读取收据或每改一次强制重读。ToolMessage 保存实际状态和已生效文件，批量补丁可能部分失败。

### Terminal Execution

一次受管理的本机 PowerShell 命令及其进程树，属于当前用户、项目、执行员工、Run 和原生任务，不以工作区创建者代替当前员工。事实沿用 `command_executions`；普通命令默认使用任务目录，`preview_service` 未指定 target 时使用绑定项目；显式 `target="project"` 或真实工作区 ID 仅选择本次目标，不改变任务默认环境。`powershell_list` 仅查询所选目录并返回该目录，不代表同项目全部工作区。临时命令按 Run/原生任务收尾，预览服务可跨 Run 查询、停止。原审批、输出游标和最多五次重试不变；原生命令保留输出及最后原生退出码，不因非零退出自动中止后续语句，依赖前步成功使用 `&&` 或显式判断退出码。

### Execution Approval

用户对某一具体执行请求的决定。保存在同一执行数据库的 `execution_approvals`，绑定执行标识、请求 hash、员工、命令和实际路径；不使用 ask，不创建新 Run。等待仍占用原员工与 FIFO，等待时间不计入当前轮有效执行时间；过期、拒绝或重启不自动执行。

### Task Publication

写回是将独立工作区成果应用到绑定目录，不等于业务验收完成。用户要求完成并交付时，接续工作区仍需处理此前未写回的修改，不因本轮没有新增编辑而忽略；沿用原权限和审批。面向用户的安装与预览使用绑定目录，工作区内服务用于该工作区测试。任务定位不将当前 HEAD 称为成果提交号，实际写回记录仍保存本次提交号。

员工调用 `publish_task_result(target?, source_workspace_ids?)` 保存正常修改，在指定已有独立工作区用原生 `git merge` 合入同项目来源与用户当前文件基线，再通过不修改 index 的 `git apply` 写回绑定目录。target 省略时使用当前独立工作区；任务默认在绑定目录也可显式指定工作区 ID，不按默认目录隐藏授权的交付工具。实际未合并索引必须先解决并暂存，或用 `git merge --abort` 中止；不扫描文本标记判断冲突。批准沿原入口，不要求验证命令 ID、服务停止或复核轮次。工具返回本次写回文件、无变化或冲突；task 复用工具 artifact 传递有界的实际写回记录，不持久化发布资格、不代表测试通过。用户 HEAD/index 不变，原地修改无需发布。

### Uploads

线程用户数据中的输入文件目录，对应虚拟路径 `/mnt/user-data/uploads`。

### Outputs

线程用户数据中的输出目录，对应虚拟路径 `/mnt/user-data/outputs`。

### Artifact

Agent 运行层工具或 Run Event 产生的文件结果。Office 不保存独立工件目录，也不提供跨 Run 的工件聚合事实源。

## 5. 执行

### Workflow

旧版 Office 按用户和项目持久化的阶段、方案、任务计划和确认决策记录。当前仅保留历史查询、退役和项目知识来源，新消息、Run 完成与启动恢复均不再自动推进这些阶段。它不是 Agent 运行层 Run、Thread、Checkpoint 或执行队列。

### Workflow Task

Workflow 中等待用户审阅或已确认的任务计划条目，包含真实 Employee ID、任务、依赖与验收标准。这是旧阶段数据；原生 task 已不通过其 `proposed/confirmed` 状态领取员工许可，历史记录不代表新的执行授权。

### Workflow Decision

旧版 PM 对用户消息的结构化解释：`APPROVED`、`CHANGES_REQUESTED`、`REJECTED`、`UNCLEAR`。旧版按来源消息、项目、Workflow 版本和合法迁移保存决策；当前原生 PM 不再生成此协议，不把普通文本或模型自称“已批准”当成执行权限。

### Workflow Revision

Workflow 每次有效方案/确认变更后的单调版本。原生任务状态回写不修改方案版本；执行轮次由 execution epoch 区分。旧版用户消息和 Run 携带此版本；当前新消息和 Resume 不再使用阶段版本准入。

### Run

一次由 RunManager 管理的 Agent 运行层图执行。状态为：

- `pending`
- `running`
- `success`
- `error`
- `timeout`
- `interrupted`

### Work Identity

`office_work` 中的用户、项目、Thread 和本次工作身份，由 Office 生成并随原生 Run Launch Contract 保存。同一工作 Resume 时保持不变，新工作使用新身份；它不拥有业务阶段、任务计划或目标完成判断。

### Work Context

`office_work_context` 中按本次工作隔离的共同背景快照，保存发起目标、最新用户要求、约束、有效决定、相关结果引用及未确认建议。派工时与当前任务及相关项目知识组合；不等于员工共享完整聊天或另一份项目长期记忆。

### Delegation Receipt

`ExecutionReceiptStore` 保存的一次原生 AI 工具调用的执行收据。由用户、项目、Thread、Work Identity、稳定 AI message ID 和 tool call ID 定位，不以父 Run ID 去重。它保存首次输入来源和真实结果；未知结果不自动重做，不拥有业务计划或目标完成裁决。

### Execution Workspace

属于用户和绑定项目的稳定独立开发目录身份，保存于现有执行事实数据库；`workspace_uses` 记录 Work、员工和调用关系。`task.workspace_mode` 默认为 `current`（通常为绑定项目），`new` 新建，`continue/fork` 通过真实 `workspace_id` 复用或分叉。私聊直接使用绑定目录，不自动分配 worktree。独立路径从开始即固定指向 `<绑定目录>/.office-worktrees/<workspace_id>/tree`，首次实际操作才准备。分叉、发布时按需快照，Run 收尾不冻结；用户原 HEAD/index 不变。

### Task File Reference

`/mnt/office-workspaces/<workspace_id>/workspace/...` 或 `.../outputs/...`。工作区文件归属按用户和项目核对，Run 访问仍经过原有员工/线程授权；用于区分同名的员工成果。尚未创建 Worktree 的 workspace 引用明确不可读，不替换为绑定目录中的同名文件。

### Thread

LangGraph Checkpoint 和连续会话的身份边界。Office Run 使用 thread ID，但没有独立公共 Thread API。

### Checkpoint

LangGraph 保存的图状态。Checkpoint 身份字段只位于 `config.configurable`。

### Run Context

`config.context` 中的请求级业务运行参数，包括服务端写入的用户、员工、模型、项目和 Office 权限事实。

### Run Launch Contract

Run 创建时持久化的可恢复启动事实，用于恢复同一执行语义。

### Run Event

RunJournal 写入 RunEventStore 的消息、模型、工具、Middleware 和 Runtime 事件。

### Runtime Event Projection

Office API 对指定员工 Run 的增量事件查询，不是另一套事件总线或跨 Run 聚合层。

### Branch

从已有 Run 的受控 Checkpoint 创建新执行。

### Resume

从已有中断 Run 的受控 Checkpoint 在同一 Thread 上创建新的 Agent 运行层 Run 继续执行。Resume 转交结构化用户回答或继续用户暂停的原生执行；它不承担旧 Workflow 阶段确认，也不新增业务完成裁决。

## 6. Agent 运行层能力

### Lead Agent

由 Agent 运行层工厂创建的主执行 Agent。

### Subagent

由 Agent 运行层原生 `task` 工具和 SubagentExecutor 启动的委派执行单元。

### Middleware

围绕 Agent 模型、工具和状态执行的 Agent 运行层中间件。

### Sandbox

工具执行与文件访问边界。Provider 可以是本地、容器或远程实现，但必须遵守相同用户和路径隔离。

### Skill

位于 Skill 存储中的 `SKILL.md` 能力说明和相关资产。PM、普通员工和委派员工初始取得获准 Skill 的名称、基本描述和 `SKILL.md` 路径；模型自行选择后用 `read_file` 按需读取正文，不自动预载全部正文。Skill 的 `allowed-tools` 只作为外部格式元数据，不参与工具选择或执行授权；主 Agent 的显式 slash 激活沿用现有机制。

### MCP Server

通过 `extensions_config.json` 配置的 Model Context Protocol 工具提供方。

### Memory

Agent 运行层当前 Memory 后端及其用户/员工范围数据。自动记忆（middleware 模式）下，员工直接对话沿用对话提炼；PM 委派在首次收尾时将任务、执行状态、工具操作摘要和员工汇报送入同一员工记忆队列，委派来源与用户原话分开标注。Memory 是异步提炼的经历摘要，不承担员工配置、项目绑定或 Run 状态职责，也不替代执行证据。

### Project Knowledge

Office 按用户和 Project Binding 隔离的项目长期记忆。事实入口不依赖业务阶段，接收带消息/Run 来源的用户原话或带任务/Run 来源的真实工具观察；本次任务要求默认只保留在工作背景。旧 Workflow 中已持久化的确认与验证通过只读来源适配器接入；旧确认/验证生产代码已删除，来源继续保留。同一项目的后续工作可以复用，历史验证不保证当前产物通过。来源和时间持久化保留；同主题按确认优先级及时间更新，无法确定的冲突保留为 `unresolved`。AI 整理只生成受校验的原文摘录，不改变确认、验证或执行权限。它不替代 Employee 的群聊/私聊 Memory。

## 7. 配置边界

### 界面语言

Desktop 在用户目录的 `language.json` 保存唯一界面语言偏好，只支持 `en` 和 `zh-CN`。Client 与 Desktop 使用 `contracts/locales/` 的同一份资源；语言影响显示及招聘初始文案，不改写既有员工配置、聊天记录、文件或 Run。

### `config.yaml`

模型、工具、Sandbox、Skill、Memory、数据库、Run 事件、调度、流桥和安全配置。

### `extensions_config.json`

MCP Server、MCP Interceptor 和 Skill 启用状态。

### `config.context`

唯一业务运行参数容器。

### `config.configurable`

只保存 `thread_id`、`checkpoint_id`、`checkpoint_ns`、`checkpoint_map`。

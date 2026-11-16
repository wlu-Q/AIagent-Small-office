# DeerFlow 上游来源

## 来源

- 项目：DeerFlow
- 上游仓库：`https://github.com/bytedance/deer-flow.git`
- 接管 commit：`b68e1c686a0cb5a3780089d27354354533451d8e`
- 接管日期：`2026-07-15`
- 许可证：MIT，副本见 [DEERFLOW_LICENSE](DEERFLOW_LICENSE)

## 接管范围

仓库接管并本地维护：

- `backend/app`
- `backend/docs`
- `backend/packages`
- `backend/samples`
- `backend/scripts`
- `backend/tests`
- `backend/pyproject.toml`、锁文件、Makefile、Dockerfile 等后端工程文件
- 根目录 `skills/public`
- `config.example.yaml`
- `extensions_config.example.json`

这些文件已经是当前仓库源码，运行时不得依赖同级外部 DeerFlow checkout。

## 小小办公室本地层

本地产品层主要位于：

- `backend/app/gateway/routers/office.py`
- `backend/packages/harness/deerflow/office/`
- Office 上下文、权限、员工运行装配和 Run 投影相关接缝

本地层复用 DeerFlow Agent、工具、Skill、MCP、Sandbox、Memory、Subagent、Checkpointer、Store、RunManager 和 RunJournal，不维护平行执行循环。

共享 `ToolErrorHandlingMiddleware` 补齐当前 LangChain 对 `invalid_tool_calls` 的处理：匹配原调用 ID 返回错误 ToolMessage，再通过 after_model 接回原生图；同步/异步保持一致，不修猜参数或额外循环。模型调用/Token/安全停止钩子先执行并清理非法工具意图，员工最终结果提取不把非法调用前的准备文字算成功。同步上游时核对原生同类支持，避免重复恢复与预算绕过。

Office 公开过程文字复用 RunJournal 的主 Agent 响应及原 `subagent.step`。`utils/messages.public_ai_text` 为回复摘要、私聊历史和委派步骤提供一致的公开文本过滤；隐藏、摘要及推理块不能作为聊天正文。Office 主回复正文通过原串行写入链及时落库，非 Office 仍按原批次刷新。同步时保留委派步骤的安全正文、原消息 ID 和服务端员工/任务身份；不得从旧原始步骤文本补放公开正文，也不为展示创建新消息、模型调用或事件存储。

Office 文件入口复用原生工具和执行事实库，解析绑定项目或显式独立 worktree。`office/workspace_operations.py` 已删除；Office 不使用历史读取收据和额外操作白名单。补丁保留上下文匹配、路径、Secret 和取消安全；非 Office 原读前写行为不变。进程树复用 `sandbox/local/managed_command.py`，Office 旧 bash 拒绝执行；同步不得恢复重复操作标记或文件锁。

工作区简化将稳定工作区与使用关联分开，删除按批次共享基线。原生 `task` 用 `workspace_mode=current/new/continue/fork` 和 `workspace_id` 选择环境；私聊与默认委派直接使用绑定项目。分叉/发布时按需快照，Run 收尾不冻结。`migrate_workspace_identity.py` 是显式离线迁移入口，并清理旧依赖字段；运行时不自动转换旧库。

`managed_command.py` 的 Windows Job Object、启动前入组和有界管道由同步旧命令与新终端会话共享。`sandbox/output.py` 先增量解码、跨块脱敏，再保留固定开头；Office 另保留有界滚动尾部，终态单独返回，不改变游标。普通命令和工具读取默认等待 30 秒，预览默认 1 秒，显式范围 0–120 秒；内部取结果不二次等待。模型返回精简字段，完整归属仍在原库。`office/terminal.py` 与四个 PowerShell 工具不生成命令、不做业务调度；保留原授权、批准等待豁免、进程树清理、未知结果不重放和五次重试边界。离线 `migrate_workspace_identity.py` 可清除工作区旧资格/缓存状态，保留原目录、Git 和执行历史，不在运行时转换。

PowerShell 普通命令默认沿任务目录；`preview_service` 省略 target 时在工具入口选择绑定项目，显式 target 仍有效，不修改任务环境、不自动发布或重启服务。list 返回查询目录，即使结果为空也不代表其他 worktree 没有服务。进程包装关闭原生非零退出转终止异常，保留正常管道输出和最后原生退出码；PowerShell 自身未处理错误仍停止，使用 Text 输出避免 CLIXML。同步不得恢复统一 exit 1 的原生命令错误包装或按命令文本推测目录。

`DanglingToolCallMiddleware` 复用逐调用结果补齐：模型请求保留历史工具调用及真实结果；Office 缺失的 task 结果按用户、项目、Thread、原 Run、AI 消息与工具调用 ID 读取已有终态收据，在原结果位置恢复状态、有界报告及已有执行事实。没有对应终态收据时仍返回结果未知，不推断成功。task 返回还复用收据 evidence 中最后六项非终端、非发布工具操作摘要；apply_patch 仅从其结构化返回提取实际 applied 操作与路径，不读取文件正文、不复制补丁全文，不把未发布理解为未编辑。原始 Checkpoint、真实 ask 恢复、图执行入口不变，不重放历史工具，不替换真实 ToolMessage，也不追加另一份执行记忆。同步保留孤立 ToolMessage 和非法工具参数的原协议处理。

`office/workspace_delivery.py` 与 `delivery_tool.py` 使用原生 Git 合并与文件写回：`publish_task_result` 保存正常员工修改，使用工作区中的 `git merge` 合入同项目来源与用户当前文件基线。冲突保留原生未合并索引及 `MERGE_HEAD`；未解决时不自动暂存，不造冲突提交，不扫描文本标记。用户当前文件仍通过临时 index 和内部引用形成基线，绑定目录只用不修改 index 的 `git apply`，没有备用覆盖路径。批准沿原入口，固定 Git 操作禁用仓库扩展程序。同步不得恢复发布资格、收尾快照、验证命令 ID 或服务停止门禁。任务、记忆与外置摘要引用工作区定位及实际执行记录，不复制一份 Git 状态或判断测试通过。

`agents/middlewares/office_round_budget.py` 沿原 Agent middleware/Checkpoint 实现 Office 单轮 900 秒有效执行预算，非 Office 保留原整任务超时。同步保留 ask/批准/task 等待豁免、并行时间不叠加、恢复已耗预算及实际取消收尾。接续用真实工作区 ID，不恢复 `task.dependency_refs`、旧终态或活动命令资格检查；原失败/超时收据保留原状。

终端 ToolMessage 的 `artifact.terminal_execution` 保存执行 ID / 真实状态，不从命令输出提炼。Office 主 Agent 和委派员工不装配 LoopDetectionMiddleware：修复文件后重复运行测试不是无进展，工具次数也不是任务完成判据。非 Office 保留原中间件；Office 原模型调用/Token 预算、单轮计时、用户取消和终端五次重试限制继续生效。同步不得恢复误导模型立即收尾的重复调用警告。

PM 通过 tools.yaml 明确选择 `powershell_list/read` 查询现有终端，默认不授予 run/stop。查询不要求 process.execute，不派生发布能力；员工页面展示只读查询项，终端控制仍禁用。已安装 PM 使用 `scripts/grant_pm_terminal_queries.py` 停服迁移，不在运行时偷偷注入。候选端口不是服务资格，不要求重启已可用但端口不同的服务。

`sandbox/patch.py` 与 `patch_tool.py` 是本地维护的普通 LangChain 文本补丁工具，格式参考 Codex 的公开补丁语法，解析及安全应用由本仓库实现。它替代 AI 可调用的旧编辑包装器，当前仅支持 LocalSandbox；Sandbox 的底层 `write_file` 方法和 `skill_manage(action="write_file")` 属于独立调用链，继续保留。`apply_patch`、`publish_task_result` 与终端共用 `execution_environment` 解析显式 target；主 Agent 和子员工不按默认目录剥离已授权的发布能力。交付工具 artifact 随原委派收据传递有界操作事实，不新增发布状态或收尾检查。同步上游时不得恢复目录过滤、重新注册旧编辑工具或绕过补丁上下文匹配和路径安全；已有安装通过 `backend/scripts/migrate_patch_tools.py` 显式维护迁移。

## 独立资料库模块

`grep` 保留原搜索引擎，默认每页 100 条、单页上限 300，显式 `max_results` 覆盖配置默认值。`offset` 跳过匹配行而非文件行号，Local/AIO 都按稳定遍历顺序分页，多读一个匹配判断末页；返回 JSON 中的 `has_more`、`next_offset` 和扫描遗漏计数。指定文件行范围仍使用 `read_file`。同步不得恢复配置默认值对显式页大小的静默截断，不增加搜索会话、结果去重门禁或旧文本输出兼容分支。

`backend/office_library/` 是小小办公室自有 RAG 模块，不属于 DeerFlow 上游接管范围，不依赖 deerflow/app。Gateway 路由、deerflow.office.library_adapter 和工具注册仅为薄适配；同步上游时保留工具合同及不写项目文件的执行分类，不改变原生 Agent loop。

Skill 目录由 `skills/prompt.py` 渲染获准名称、描述和路径，主 Agent、委派员工与嵌入客户端共享，正文按需读取。原 `describe_skill` 及其专属查询 catalog 已删除，不恢复工具查询中转或全文预载。已安装员工通过 `scripts/remove_retired_skill_tool.py` 离线删除退役工具选择，运行时不加兼容分支。`report_to_pm` 保留参数与上报职责，但不再 return_direct；工作项、文件与未完成验收项使用实际数组，省略时为空数组，不接收 null 或 JSON 字符串，参数错误沿原生 loop 纠正。task 返回不附加重复的业务核验指导，执行完成文案不代表验收通过。

## 本地数据

`backend/.deer-flow/` 以及由 `DEER_FLOW_HOME` 指向的数据目录是本地运行状态，不提交 Git。删除这些目录会删除本地持久化事实。

没有配置有效模型密钥时，Gateway 和不依赖模型的 API 可能仍能启动；这不能证明真实 Agent Run 成功。

## 同步规则

Office 文件检索沿原权限、路径解析和结果过滤执行。`RetrievalEvidenceMiddleware` 仅记录任务内观察索引，并在输出预算处理后的模型输入中合并同请求、同结果的重复正文；原 ToolMessage、RunJournal 和调用 ID 不被删除。索引不自动进入 Prompt，不是项目知识或员工记忆。LocalSandbox 的只读 primitive 在路径与 Skill 权限检查之后，只合并同任务、同授权的相同在途请求，不保存跨轮执行缓存；分页、工作区和参数变化分别执行，失败不复用。正文被压缩或外置后无法确认原文仍可见时，不生成悬空引用。同步不得恢复检索次数门禁或用历史观察跳过当前搜索。

当前 Windows 构建使用 Python 3.12.13 / uv 0.11.28 和 `uv.lock` 中的依赖；依赖更新保留 `greenlet==3.3.0` 的 Windows 约束。LangChain、LangGraph、MCP 与模型 SDK 同步时，需要验证现有 Middleware、Run/Checkpoint 恢复和工具调用协议，不恢复已删除的 Office 执行层。

`review_skill_package` 的 Skill URI、虚拟路径与本地路径使用当前用户的 Skill 存储，并支持系统临时目录中的下载包；其他用户目录、路径穿越和 Skill 链接越界仍拒绝。Python 静态检查不把 URL 字符串当作网络调用，也不把单项环境配置读取当作批量导出；环境访问与网络调用仅在同文件共现时提示待核实，直接向网络调用传递环境内容仍保留高危判定。不执行被检查的脚本，不增加安装门禁。

PowerShell 工具仅对命令及输出做 Secret 脱敏，保留真实本机路径；文件工具继续使用原虚拟路径映射。技能资源审查仅对正文中明确的包内文档链接报告目标缺失，不把安装变量、命令或代码示例当作缺失文件；包外文档链接只记录信息，不读取外部目标。实际文件访问的路径安全和包读取限制不变。

Office 的 Skill 安装、创建和卸载共用当前用户的 `users/<user_id>/skills/custom/<name>/` 文件目录，由已有文件与终端工具操作，不新增安装工具或注册表。LocalSandbox 将 `/mnt/skills/custom` 映射到该用户目录；终端注入 `OFFICE_SKILLS_DIR`。公共资源仍只读，原用户隔离与路径安全保留。所有 Office AIagent 使用同一管理提示，用户配置的 Skill 名称与描述按需注入，正文仍由 `read_file` 读取。管理 Skill 时正文是操作对象，只有实际选用 Skill 完成任务时才执行其业务流程。配置页面刷新现有 Skill 列表，新对话扫描当前目录，卸载项不再展示；安装成功提示用户到员工配置勾选并保存，下次对话生效。同步时不得恢复 `find-skills` 的全局安装脚本或 `skill-creator` 的专用安装协议。

模型侧员工查询的 `tools`、`skills` 只列实际可用名称，共享 `tool_catalog` 提供简短用途、`skill_catalog` 提供启用 Skill 的元数据描述，每个名称只解释一次；执行员工仍获得完整工具说明与参数。`search_employee_registry(mode="match")` 按传入条件筛选，`mode="all"` 忽略筛选条件返回当前办公室全部启用的普通员工，两种模式均不限制人数，空闲与忙碌人员仍分开返回；不改变用户隔离和派工权限。`required_tools` 在 match 模式下仍是精确名称列表，取自 `employees[].tools`。精简后的员工列表不做通用工具结果裁剪，保留全部候选；其他结构化大结果仅在外置写盘时格式化 JSON。主 Agent 与委派员工的 MCP 延迟目录仍展示获准工具的名称和描述，完整参数继续通过 `tool_search` 按需加载。

DurableContextMiddleware 按用户、项目、Thread、原 Run 与工具调用 ID，从现有终态委派收据校正状态、实际报告及工作区引用；运行中收据不覆盖终态，unknown 不是正在执行或用户取消，不自动重放。task 重放先读取原收据，不重新申请员工执行位或加载当前模型，但仍校验原调用身份与参数。取消沿原收尾任务保存已取得的报告、工具事实及用量，再释放员工。重启继续复用 Gateway 的 mark_unresolved_unknown，不新增清理器或另一套执行状态。grep 返回匹配、显示截断及扫描遗漏计数；本地 literal 搜索不跳过长行，正则长行限制仍在，但跳过时明确搜索不完整。

委派模型用量逐调用进入原 RunJournal；跨线程回调回到原事件循环累加，以 source_run_id 去重，并通过原 llm.external.usage 事件持久化。恢复按逐调用事件重建，不取模型汇总最大值；迟到用量只补原 Run 统计，不修改结束状态、正文或结束时间。工具结果的明确元数据同步到 ToolMessage.status，子员工步骤保留 tool_call_id 和真实成功/失败；成功读取的文件正文不是执行状态。

委派收据只持久化一份执行状态；task 返回的 execution_status 和 partial 从当前收据生成，不再保存在 result_ref 中。Office PowerShell 子进程通过 JAVA_TOOL_OPTIONS 指定 Java 标准输出和错误输出为 UTF-8，保留其他 JVM 参数，不修改系统环境；输出采集、脱敏与游标不变。预览交付使用当前状态及真实路由参数，历史日志和 HTTP 200 不代表页面功能验证通过，不新增预览门禁。

本地 `TodoMiddleware` 仅补齐原生 `write_todos` 输入及嵌套字段的描述，保留原生同步/异步执行函数与完整清单替换语义；Office 装配与工具目录共用同一工具说明，不修改依赖源码或完成门禁。资料库查询、延迟工具搜索、技能包检查及终端参数的说明进入模型可见 Schema；工具搜索仍按原正则算法，技能包检查不下载远程 URL，终端 purpose 仍是固定执行类型。终端说明保留普通员工所需的预览就绪与版本区别，不要求修改前停服；退出码、执行 ID、日志尾部与服务期限按实际返回条件说明，参数和执行机制不变。同步不得将描述补充变成权限扩大、自动安装或额外执行步骤。

Office 员工查询使用当前配置和运行投影，模型看到的 `tools` 与委派装配共用 delegated 工具合同，不混入仅直接对话可用的工具或工具组名。查询和 `task` 的 `required_tools` 仅接受实际工具名；错误名称提示纠正参数，不解释为员工缺少能力，也不映射旧名称。`task` 复用员工执行位进行准入，忙碌委派不进入 FIFO，用户直接消息仍可排队。退役的 resolve_employee_routing 通过 `scripts/remove_retired_routing_tool.py` 停服显式清理，不保留运行时别名。Skill 与工具权限继续独立。工作区收尾返回定位与实际操作记录，不把当前 HEAD 作为成果提交号提供给模型；内部快照、fork 和实际写回提交号不变。不扫描差异、不自动提交、不生成每任务成果资格。委派台账的 completed 仅说明执行结束，允许按当前用户目标处理剩余工作而不重复已完成操作；共享工作区指导要求交付接续成果时处理此前未写回的修改，沿用原审批。同步不得恢复“执行结束即禁止继续委派”的指导。

ToolOutputBudgetMiddleware 在模型请求视图中把已有工具结果、且不属于最近一组调用的大参数外置到现有 .tool-results。原始 Checkpoint 参数和错误结果不变；最近一组纠错输入、待执行、无可用存储或带签名 thinking 的请求不改写参数，不返回假文件路径。更早的失败调用也可按引用读取，不把失败大补丁永久附带每次请求。保留原有结果预算和 257000 压缩阈值，不发送厂商专用的 context_management 参数。

1. 同步前记录目标上游 commit。
2. 对接管 commit、当前分支和目标上游做三方比较。
3. 不直接覆盖 Office 本地层、安全修复和用户隔离改动。
4. 上游新增能力先判断是否属于当前产品，再决定接入或删除。
5. 删除上游模块时同时删除调用者、配置、测试、数据说明和文档。
6. 任何相关代码变化后重新运行对应验证。

Firecrawl 搜索仅通过 config.yaml 配置可选密钥，Office 不提供搜索设置界面或专用 API。未配置密钥时，按既有 fallback: ddgs 配置直接使用 DDGS；配置密钥后保留既有服务错误降级和冷却行为。发行模板不导出开发者搜索密钥。

Office 用户可见的模型、员工和项目输入错误携带稳定代码及参数，HTTP 边界保留原状态码；资料库在独立模块内携带同类展示信息，不依赖 DeerFlow。Desktop 根据本机语言资源显示这些代码，不将界面语言注入 Agent 权限或执行状态。同步保留安全错误消息和不回显凭证的边界，不从历史自然语言错误反推错误代码。

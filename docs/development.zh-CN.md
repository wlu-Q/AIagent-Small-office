# 小小办公室运行与维护说明

[English](../README.md) · [中文介绍](../README.zh-CN.md) · [贡献说明](../CONTRIBUTING.md)

本文中的命令均在仓库根目录执行，代码与配置路径也以仓库根目录为基准。

小小办公室是一个本地桌面办公室项目。仓库当前同时包含：

- 已落地的agent后端能力；
- 已接入员工管理、群聊/私聊、独立员工队列、Run 过程、项目绑定和办公室画布的 Electron/React 客户端。

聊天回复来自公共群聊 SQLite 或 Agent 运行层 Thread Checkpoint；工具、制品、Subagent 和终态过程来自指定 Run 的增量 Event 查询。客户端不保存第二份对话、Run 或审批事实。

项目自有代码和自制角色素材按 MIT 发布，见根目录 `LICENSE`；保留已有第三方许可证和署名。

原生委派共享执行池最多同时运行 5 个员工任务；PM 默认每轮最多发起 5 次、单次 Run 累计最多 20 次委派，可配置更低限制。普通员工自己的忙闲和 FIFO 独立保留。超出当轮限额的调用明确返回未启动、未排队，PM 可在剩余额度内下一轮再安排；累计耗尽时保留可见提示。`npm run test:delegation-limits` 使用真实 Desktop/Gateway/员工执行链与受控模型验证并发、累计上限及委派期间私聊 FIFO，不代表生产模型会自动采用最佳分工。

### 界面语言

支持 English 和简体中文。首次启动按系统语言选择，其他系统语言使用英文；初始化页和办公室设置都可切换。语言偏好只保存在 Desktop 用户目录的 `language.json`，不写入员工配置或项目文件。切换即时更新界面与画布提示，不重建办公室，不清空输入或聊天记录。内置岗位名称、标签与招聘初始 Prompt 随语言显示，已有员工姓名、Prompt、聊天、文件及模型生成内容保持原文。

两份语言资源位于 `contracts/locales/`，随应用本地发布，不下载语言包。自有模型配置与资料库错误通过代码和参数在桌面端翻译；终端输出、第三方诊断和历史错误保留原文。缺少翻译不跨语言兜底，`npm run test:i18n` 检查词库、语言保存和真实 Electron 双语流程；`npm --prefix client run check:i18n` 仅检查词库与硬编码文案。

### 通讯界面

- 左侧会话/员工列表保持原样；右侧采用 AI 左、人右的对话布局。Enter 发送，Shift + Enter 换行，中文输入法确认不会误发。
- 过程回复、私聊和等待回复统一使用员工头像，历史群聊保留发送时的头像。只有缺少有效头像时才使用名字首字。
- 没有正文的失败/取消 Run 按终态时间插入历史位置，后续消息不会把旧状态重新推到聊天底部；历史错误仍保留可追溯性。
- 会话内容、草稿、滚动位置与 Run 事件游标保存在当前 Office 页面内存中。切回已读会话立即展示缓存，再后台同步；群聊从原序号增量读取，同一未完成查询复用请求。消息展示不等待队列和 Run 列表，刷新失败保留已有内容。切换项目、离开 Office 页面或重启应用会释放缓存，未发送草稿不落盘。
- 正文支持 Markdown 段落、列表、表格和代码块。工具只显示紧凑的命令/路径与状态，不展开原始 JSON、读取到的源码或内部推理；命令行不可点击执行。
- 执行中的公开 AI 回复按 `llm.ai.response` 分段更新，最终消息按 ID/所属 Run 去重。这是模型每轮回复完成后的实时投影，不是逐 token 流式；重进会话恢复每位员工最近 8 个 Run 的过程记录。
- 追问在主输入框回答；普通员工运行中可在发送区“停止本次执行”。群聊暂停/继续与普通员工私聊隔离；客户端通过原生 Run 关联跟踪执行，不再显示固定阶段。两种停止都不会撤销已发生的文件改动。
- Markdown 文件链接与产物显示文件卡片和实际大小，点击打开只读预览。代码支持高亮、行号、搜索和复制，Markdown 支持阅读/源码切换，常见图片支持预览；文件标签可切换、关闭，预览可最大化，并保留文件夹定位。打开或刷新时读取当前内容，不保存历史文件副本。
- 聊天与 Markdown 阅读共用图表展示：`mermaid` 围栏支持流程/架构图和时序图，`office-chart` JSON 围栏支持柱状图、折线图、饼图，可放大预览；格式错误只影响该图并显示原因，不展开图表源码。图表由本地组件生成静态 SVG 图片，不执行模型提供的 HTML/JavaScript，不需要 Bash 或外部图表服务。公共 `office-visualization` Skill 提供输出规范，新 PM 默认配置，已有员工仍通过个人 Skills 配置选择，启动不会覆盖用户的选择。
- 正文文件引用显示为行内链接，独立附件保留文件卡片；过程引用保留实际工作区来源，不用绑定目录同名文件替代缺失文件。
- 文件预览通过 `/api/office/files/preview` 校验项目边界、员工与 Run 归属；项目虚拟路径映射到绑定目录，旧 Run 不会误读新项目同名文件。文本上限 2 MB、图片上限 10 MB，超限或不支持格式提供定位入口。HTML/SVG 只展示源码，外部图片不自动加载。HTTP/HTTPS 链接经受限 IPC 打开系统默认浏览器，点击不会自动启动本地项目服务。
- `react-markdown` 使用 `remark-gfm`；Vite 7 通过标准包导出解析依赖，不再固定旧版 `vfile`。

### 模型管理

办公室设置提供固定高度的模型列表和“添加模型”弹窗，支持 OpenAI（ChatGPT）、DeepSeek、MiMo、Kimi、GLM、豆包和 MiniMax。模型 ID 手动输入，API 地址预填官方地址并允许修改，图片输入能力由用户明确选择。添加时执行两轮无项目副作用的工具调用验证；通过后独立保存，不自动切换全局默认。失败原因通过 Toast 返回，API Key 不在列表或错误详情中返回。

模型行悬浮或键盘聚焦时显示“编辑”（触屏常显），复用弹窗修改模型名称、API 地址、密钥、图片能力和思考配置；API Key 留空保留原值。连接或思考配置变化时重新验证，保存保留模型 ID、添加时间、默认状态和员工绑定。有员工正在使用该模型工作或等待用户回复时禁止修改。

模型列表的“思考”列显示保存的配置。思考选项按厂商统一提供：MiMo 使用开关，其他厂商使用各自的原生档位；界面只翻译标签（如 `high` 为“高”、`max` 为“最高”），请求保留原值，不维护逐型号能力表。PM、直接对话员工和委派员工使用其绑定模型的思考配置；不同型号及第三方接口是否接受并实际执行该参数，仍取决于对应服务。

“全局默认”用于新建员工：创建时保存当前默认模型，之后切换全局默认不影响已有员工。员工列表中的“使用模型”下拉框显示“厂商 - 模型 - 思考配置”，点击“保存配置”后生效，工作中禁止修改。旧版跟随默认的员工首次启动时固定为当前有效模型，已有独立绑定保留。已启动 Run 和 ask 恢复继续使用原合同中的模型。模型配置继续保存在 Agent 运行层 `config.yaml`；历史配置缺少添加日期时显示“历史配置”。

`npm run test:model-settings` 构建并验证 Electron 页面交互、真实 Gateway/HTTP 模型协议、默认切换、ask/FIFO 和派工。该集成测试使用隔离数据与受控模型回复，不访问正式办公室，也不代表七家模型商的所有真实模型已经通过验收。

`npm run test:employee-model` 验证员工模型下拉框、未保存关闭、保存失败、工作中禁用，以及通过真实 Gateway 保存员工绑定、新建员工复制默认、已有员工保持绑定和 ask/FIFO/委派使用原模型。

### 办公室资料库

员工列表下方入口打开统一书架。支持上传、名称搜索、删除和失败重试；桌面端一排五本，封面随指针轻微倾斜，点击翻开进入原文件阅读。无章节侧栏、下载按钮；小窗口、键盘操作与减少动画设置保持支持。

TXT/Markdown、PDF 文字层及 DOCX 正文进入独立 RAG 模块；忽略图片、不做 OCR。单文件不超过 20 MB，无文字时保留原文件但不发布索引。同名同类型文件要求确认替换，内容相同而名称不同仍允许上传。替换成功前旧版保持可检索。资料及索引持久化在用户目录 office-library 下，可跨同一用户的项目使用。

PDF 复用本地 PDF.js，DOCX 复用隔离的 docx-preview，复杂 Word 排版不保证完全一致。检索结果提供“引用来自：文档名【位置】”供员工在回答中引用：TXT/Markdown 使用原文件行号范围，PDF 使用页码，DOCX 使用内容块；位置对应返回片段，不是模型逐句推定的引用。聊天来源链接打开对应历史原文件版本；资料删除后旧引用失效。TXT/Markdown 预览正文上限 2 MB。

核心 `backend/office_library/` 采用 SQLite + LanceDB，解析、切片、批量向量化后发布索引。检索通过关键词与向量召回、RRF 融合、片段去重、Qwen 精排和相邻片段合并，返回原文及来源。模型配置独立于聊天模型；向量模型变更进行全库重建，成功后统一切换。接口失败有明确错误或降级状态。云端向量、精排和聊天模型会接收相应文本。

员工通过 `search_office_library(query)` 自主检索。新员工默认配置；已有员工升级前停机执行 `backend/.venv/Scripts/python.exe backend/scripts/enable_office_library.py --data-dir backend/.deer-flow --maintenance` 后重启。工具仍受 tools.yaml 控制，用户之后移除不会重新授予。资料不自动写进员工记忆或项目知识，不改变 FIFO、ask 或委派方式。

资料库向量与精排分别配置模型、接口地址和 API 密钥；密钥留空只保留对应模型的原密钥，不互相借用。Windows 使用本机 DPAPI 加密，macOS 使用 Keychain 保存资料库加密密钥；接口只返回两项已配置状态。仅更换密钥或精排配置不重建向量索引。旧共享密钥升级时先停止 Gateway，运行 `backend/.venv/Scripts/python.exe backend/scripts/split_library_api_keys.py --database <资料库目录>/library.sqlite --apply --maintenance`；脚本备份数据库，将原加密密钥复制到两个独立字段，并保留待重建配置、文档与索引。两种平台也可分别使用 `OFFICE_LIBRARY_EMBEDDING_API_KEY` 和 `OFFICE_LIBRARY_RERANK_API_KEY` 环境变量提供尚未保存的密钥。

用 `npm run test:library` 验证隔离 IPC 下的 Electron 界面，用 `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_office_library.py` 验证资料库后端。

## 当前执行链

后端员工 Run 的真实执行链：

```text
/api/office/*
  -> OfficeMessagePipeline
  -> EmployeeRunGate
  -> OfficeRunLauncher
  -> app.gateway.services.start_run
  -> RunManager
  -> Agent 运行层 lead agent / worker
  -> Checkpointer + RunStore + RunEventStore
```

Run 查询由 `OfficeRunView` 投影 Agent 运行层事实，员工状态与群聊最终回复由
`OfficeRunProjection` 消费 Agent 运行层生命周期 hook；Office 不保存第二份 Run、Thread、
Checkpoint 或 Event。

澄清使用 Agent 运行层原生 interrupt/resume：用户在原聊天框正常回复后，Gateway 在同一
Thread 上创建新的 Resume Run；员工忙碌时复用同一个员工 FIFO。

PM 按目标选择授权工具、独立 Skills 和真实员工，工具/员工结果回到同一原生 Agent loop。
员工工具选择保存在员工目录的 `tools.yaml`，通过 `/api/office/tools` 与员工工具接口读取和更新；工具定义提供描述、来源和可用性，Authority 继续负责服务端安全上限。初始 Skill 目录直接提供已授权名称、简述和文档路径，选中后用 `read_file` 读取正文，不需要 `describe_skill`。Skill 只提供按需的工作方法说明，Skill frontmatter 中的 `allowed-tools` 不改变工具集合。旧员工首次运行前需执行工具清单迁移，缺失清单不会静默加载全部工具。移除 Skill 查询工具的已有安装需在停止 Gateway 后执行 `backend/.venv/Scripts/python.exe backend/scripts/remove_retired_skill_tool.py --data-dir backend/.deer-flow --apply --maintenance`（设置 `PYTHONPATH=backend/packages/harness`）；脚本只删除退役选择并保存备份，不改变其他授权。
最终回答结束 Run，不再消费阶段方案或自动创建下一阶段 Run。有效 ask 从所属原生
Checkpoint 识别，等待期间 PM 保持忙碌，匹配回答复用员工 FIFO 恢复；其他员工不被整组冻结。
手动暂停与 ask 等待分别记录，回答不能解除手动暂停。显式继续可恢复 PM 原生 Checkpoint，
普通员工的直接中断不自动重放。来源 Run、问题 ID、项目和 Checkpoint 必须匹配。

历史 Workflow 记录保留在 `office-workflows.v1.sqlite3`，只用于查询、退役和历史知识投影。
项目知识位于 `office-project-knowledge.v1.sqlite3`，按用户、项目和任务范围提供。
原生执行的真实工具观察进入历史证据；PM 未获认可的建议不自动变成确认事实。
模型只接收这些历史观察的事实字段，不重复附带原始工具 evidence；完整证据仍在原库中。此区分不截断聊天历史、员工记忆或改变 257000 压缩阈值。
Run 成功后的知识整理复用现有数量/冲突阈值；整理失败保留原始事实，历史验证不代表当前产物通过。

桌面渲染链：

```text
React + Phaser
  -> window.officeDesktopBridge
  -> Electron preload
  -> Electron main
  -> Gateway health / project / office API
```

桌面桥当前按静态白名单暴露：

- Gateway 状态与探活；
- 当前项目摘要与目录绑定；
- 员工模板、办公室模型、员工创建/个人信息/删除（个人信息仅含 idle 员工的姓名、Prompt、Skills 和模型绑定）；
- 群聊、私聊、消息发送和澄清 Resume；
- 员工 FIFO 的查询、编辑、删除，以及 Run 查询、事件增量读取和取消；
- 窗口缩放；
- `service.status`、`workspace.updated` 两类事件。

## 目录职责

- `backend/`：Gateway、Office 领域服务、Agent 运行层、工具、Skill、MCP、Sandbox、Memory、Run 与持久化。
- `client/`：React + Phaser 办公室画布、员工管理、聊天、队列、Run 过程、项目面板、缩放和 Toast。
- `desktop/`：Electron 主进程、preload、Gateway 生命周期和受限 IPC。
- `skills/`：Skill 资产。
- AI 安装或创建的 Skill 保存到当前运行数据目录 `users/<user_id>/skills/custom/<name>/`，不是项目目录。员工配置自动刷新列表；勾选并保存后，下次对话生效。用户要求卸载时删除对应自定义 Skill，列表随目录变化刷新。内置 Skills 保留在 `skills/public/`。
- `contracts/skill_review/`：Skill 审查数据合同。
- `contracts/desktop-gateway-status.json`：桌面状态字符串合同。
- `output/`：设计素材和调试产物。
- `backend/.deer-flow/`：本地运行数据，不提交 Git。

## 本地运行

支持 Windows x64 和 macOS 15+ 的 Apple Silicon（arm64）设备。两种平台使用 Node.js 24.22.2、npm 11.19.1、Python 3.12.13、uv 0.11.28；Mac 使用原生 arm64 工具链。版本分别记录在 `.node-version`、`package.json` 和 `backend/.python-version` / `backend/pyproject.toml`；依赖由两份 `package-lock.json` 和 `backend/uv.lock` 锁定。

```powershell
npm ci
npm --prefix client ci
uv sync --directory backend --locked
```

首次启动前，按 [README 的平台步骤](../README.zh-CN.md#development)复制无密钥配置并准备运行时。Windows 运行 `npm run runtime:powershell`；macOS 运行 `npm run runtime:prepare`，准备 Node、Git 和 PowerShell。之后执行 `npm run dev:check`。

下文的 npm 和 uv 命令可在 PowerShell 或 macOS 终端执行。示例中的 `backend/.venv/Scripts/python.exe` 是 Windows 虚拟环境入口；macOS 对应 `backend/.venv/bin/python`。

单独启动 Gateway：

```powershell
npm run deerflow:gateway
```

独立 Gateway 与 Desktop 共用启动环境，优先加载 `backend/packages/harness` 当前源码；默认使用根目录 `config.yaml` 和 `backend/.deer-flow/` 数据目录，可通过 `DEER_FLOW_CONFIG_PATH`、`DEER_FLOW_HOME` 覆盖。

搜索服务由 `config.yaml` 的 `web_search.use` 选择。Firecrawl 适配支持配置 `fallback: ddgs`：额度不足时冷却 1 小时，认证或访问失败时冷却 5 分钟，限流、网络及服务异常时冷却 1 分钟；期间使用 DDGS，冷却后重新尝试 Firecrawl。结果包含实际 `provider`，两家都失败时返回明确错误。API Key 只保存在被 Git 忽略的本机配置或环境变量中，配置修改后重启 Gateway。

开发模式在两种平台均可通过以下命令启动；Windows 也可双击根目录 `start-dev.cmd`：

```powershell
npm run dev
```

`npm run dev:check` 只检查环境，不下载或安装运行时。开发入口不执行生产构建；它校验 Electron、Vite、后端虚拟环境、配置和自带 PowerShell，macOS 还校验原生 arm64 架构及自带 Node、Git，然后启动 Electron。根目录 `npm ci` 的 postinstall 使用 Electron 自带安装器下载并校验固定版本的桌面运行时。运行时版本、官方地址和 SHA256 分别固定在 Windows 的 `desktop/packaging/runtimes.json` 与 macOS 的 `desktop/packaging/runtimes.macos.json`；归档与解包目录位于忽略的 `output/packaging/`。正确的归档缓存直接复用，固定版本变化时替换对应构建目录。Electron 在本进程中通过 Vite 的 ESM API 启动开发服务，使用仅监听 `127.0.0.1` 的系统分配端口，等待监听成功后加载当前仓库页面，再沿用既有 Gateway 生命周期。启动地址打印在日志中，不会连接其他项目占用的 5173 页面。`npm run desktop:start:dev` 使用同一入口。

首次初始化必须选择工作区目录并测试模型连接后才能进入。目录选择复用现有项目绑定接口，取消选择不改变已有绑定，重新打开引导页会显示已选目录；模型连接失败不清除目录。现有已初始化办公室不重新走引导，也不改动原工作区、员工或聊天数据。

同一 Electron 应用只允许一个实例，重复启动会聚焦已有窗口。开发模式下，8001 被占用时会明确报错；确需使用已有 Gateway 时，启动前设置 `DEER_FLOW_GATEWAY_URL`，由用户明确选择目标服务，应用退出时不关闭该外部服务。

Windows 关闭最后一个窗口会退出应用，macOS 关闭窗口后应用继续运行，可从 Dock 重新打开；在 Mac 上使用应用菜单退出或 Command+Q 结束应用。应用正常退出时停止自身启动的 Gateway 进程树并关闭 Vite。启动终端收到 Ctrl+C/终止信号时清理本次 Electron 进程树，不按端口或进程名称结束其他服务。操作系统强制结束进程或断电不提供正常退出保证。

启动日志位于 `output/logs/development.log`；Gateway 日志位于 `backend/.deer-flow/logs/gateway.log`。缺环境/配置时会显示处理提示；双击脚本失败会保留终端，便于查看错误。

使用构建后的前端启动现有桌面壳：

```powershell
npm run build
npm run desktop:start
```

开发 Vite 只在未打包且带 `--office-dev` 参数时加载，普通启动使用 `client/dist`。Windows 与 macOS 安装包沿用同一 Client、Desktop 和 Gateway 代码，运行时与用户数据位置见下方打包说明。

## 验证入口

启动检查与真实 Vite/进程树清理验证：`npm run dev:check`、`npm run test:startup`。

桌面与客户端最小验证：

```powershell
npm run test:minimum
npm run test:task-workspaces
```

后端测试在仓库根目录使用 Windows 的 `backend/.venv/Scripts/python.exe -m pytest ...` 或 macOS 的 `backend/.venv/bin/python -m pytest ...`，也可在 `backend/` 中使用 `uv run pytest ...`。

跨平台运行时与打包逻辑检查为 `npm run test:runtimes` 和 `npm run test:packaging`。当前 GitHub Actions 仅配置 Windows CI；Mac 需要在 Apple Silicon 设备上执行启动、桌面交互与打包验证。

PM 目标执行与历史读取验证（受控模型，不调用远程服务）：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_office_pm_goal_flow.py backend/tests/test_office_workflow_history.py backend/tests/test_office_workflow_integration.py -q
```

Run 查询中的调用数、消息数、Token 和 `lastEventSeq` 是原生 Worker 收尾时提交的统计。运行期间应使用增量事件观察执行，不能将统计暂为零解释为没有调用模型或员工。

构建、静态检查和单元测试不能替代真实模型 Run、Gateway 网络调用或可见 Electron 交互验证。

## PM 目标执行

固定阶段控制器已移除：PM 装配和原生 `task` 已不再要求阶段计划或 `workflow_task_id`，可按目标委派真实员工完成分析、实现、测试、Review 或文档。委派仍同步等待结果，保留权限、员工 FIFO、共同背景与持久化防重复收据；委派员工向 PM 上报，由 PM 统一询问用户。

私聊直接读写绑定项目；PM 原生 `task` 默认为 `workspace_mode=current`，明确选择 `new` 才新建 worktree，`continue/fork` 通过 `workspace_id` 复用或分叉。目录位于绑定项目内部 `.office-worktrees`，不使用文件锁，不按工具权限隐式换目录。独立成果通过带工作区 ID 的引用预览，默认任务产物直接来自绑定目录。

员工文本编辑使用普通 LangChain `apply_patch(patch)`，支持新建、修改、删除、移动和多文件补丁。Office 删除强制“读一次才能改一次”，保留上下文匹配、80 KiB 补丁/8 MiB UTF-8 文件限制及路径安全，不跟随链接或覆盖已有移动目标。返回真实状态与已生效清单，不隐式回滚。非 Office 保留原有读前写中间件。

已有安装需停机维护迁移工具清单，不在运行时隐式兼容旧名称：先设置 `$env:PYTHONPATH='backend/packages/harness'`，执行 `backend/.venv/Scripts/python.exe backend/scripts/migrate_patch_tools.py --data-dir backend/.deer-flow --config config.yaml` 预检；确认员工无运行/待答工作、队列为空并关闭 Gateway 后，加 `--apply --maintenance` 执行。迁移保留其他工具选择与权限，PM 不会获得编辑权限。相邻 `*.before-apply-patch` 备份已排除 Git；中途失败先保持停机并核对逐员工清单，回退程序版本时配套恢复这些备份，不在新版本恢复旧工具别名。

独立 worktree 通过 `publish_task_result(target?, source_workspace_ids?)` 按真实 Git 祖先合并并写回绑定目录，保留用户文件、HEAD 和 index。任务默认在绑定目录也可以用 target 指定已有工作区；`apply_patch` 和终端同样支持显式目标，工具授权不随默认目录改变。不要求验证执行 ID、停止服务或无冲突后额外复核。冲突先留在员工目录处理再回写，失败不以复制覆盖作为备用路径。原地修改无需发布。

独立 worktree 支持普通 Git 根目录；非 Git 项目按办公室权限执行 `git init --template=`，不暂存或提交用户文件。Git 子目录绑定、子模块、符号链接和 sparse checkout 不支持独立 worktree。默认原地工具不要求 Git 仓库。工作区需同一用户和项目，可跨 Run/员工复用，不要求相同 Work、基线或前任务成功。创建 worktree 不安装依赖或复制 `.env`/node_modules。

员工管理按能力组展示：读取文件（`read_file/ls/glob/grep`，默认勾选且不可取消）、编辑文件（`apply_patch`）、网络搜索（`web_search/web_fetch/image_search`）、办公室资料库（`search_office_library`）、终端控制（装配 `powershell_run/read/list/stop`）。PM 另有终端查询（只读）和任务计划，可配置编辑；终端控制在前端保持禁用。能力组仍受员工 Authority 限制，部分启用的可选组显示部分选中，勾选或取消统一调整组成员；其他已有底层及扩展工具配置保留。`tools.yaml` 与 Run 使用实际工具身份，统一点击“保存配置”保存。成果发布只随编辑/命令执行授权在独立工作区装配，终端查询不附带发布能力。办公室设置默认请求批准，切换完全访问先显示本机权限警告；请求按原 Run 所属会话显示在底部输入区，提供“拒绝／允许一次”，处理后恢复输入框和草稿。PM 委派请求归属群聊，其他会话有待批准提示。等待保留原 Run/FIFO，过期与重启不重放。这里没有操作系统级权限沙箱。

验收入口 `npm run test:terminal` 和 `npm run test:delivery` 构建 Client 并启动真实 Desktop/Gateway，分别验证会话管理和批准/冲突集成/交付。使用隔离目录和受控模型，不使用生产密钥或项目；真实绑定模型的整项目验收另行执行。

Windows 与 macOS 的员工终端均使用 PowerShell 7.4+；桌面开发版和安装版向 Gateway 传入自带 `pwsh.exe`（Windows）或 `pwsh`（macOS）的绝对路径，开发者可用 `OFFICE_POWERSHELL_EXECUTABLE` 显式覆盖。独立启动后端时仍支持该变量或系统 PATH 中的 `pwsh`。会话记录复用 `command_executions`；普通命令及 `powershell_read` 默认等待完成或 30 秒，预览服务默认等待 1 秒，可显式设置 0–120 秒，不因每行日志提前返回。stdout/stderr 分开、有界留存脱敏后的开头和尾部；增量游标只对应固定开头，终态单独返回错误尾部与省略数量，退出码不从文本猜测。同一失败链最多重试五次，重启留下的在途状态记为 unknown，不重放、不按旧 PID 杀进程。

终端使用当前主机的原生路径，文件工具的 `/mnt/...` 引用不能直接用于 PowerShell；普通命令默认沿任务目录，`preview_service` 省略 target 时默认在用户绑定项目启动，显式 `target="project"` 或工作区 ID 仅改变本次执行目录。list 返回所查询的目录，不跨工作区汇总。原生命令保留输出与最后原生退出码，不因非零退出自动停止后续语句；依赖前步成功使用 `&&` 或显式判断 `$LASTEXITCODE`，PowerShell 自身未处理错误仍中止。`purpose=preview_service` 不向模型分配端口，实际监听地址以命令输出为准，不因端口差异重启可用服务。终端通过 `ToolMessage.artifact` 传递真实执行状态。Office 主 Agent 保留 LoopDetection 工具循环限制，并放宽相同调用组合的重复阈值；受委派员工沿用其独立运行链。模型调用/Token 预算、单轮计时、取消和终端重试限制继续生效。相关验证见 `backend/tests/test_office_terminal.py` 和 `backend/tests/test_office_preview_recovery.py`。

临时命令在工作收尾前停止；预览服务可保留到显式停止、取消、超时或 Gateway 退出。发布不要求停止服务，不认证测试结果。PM 工具清单默认提供 `powershell_list/read`，员工管理显示“终端查询（只读）”；“终端控制”仍禁用。查询不要求执行权限，也不派生发布能力。重新提供历史地址时查询当前记录；running 不是 HTTP 就绪证明，刚取得的有效执行结果不重复检查。现有安装停服运行 `backend/scripts/grant_pm_terminal_queries.py --data-dir backend/.deer-flow --apply --maintenance` 并开始新 Run，脚本备份原清单；默认仅 dry-run。当前不支持交互式 stdin、密码提示和 TUI，Worktree 不提供 OS 权限沙箱。

前后端已接通原生结束、ask 等待、暂停恢复及当前 Run 关联。通讯界面保留工具、员工活动及历史任务验证记录，不再显示固定阶段。隔离环境的完整链路可通过 `npm run test:gateway-integration` 验证；该命令使用受控模型，不证明真实模型交付能力。旧阶段控制、结构化回复协议和执行模块已删除；旧数据库仅作为历史读取和知识来源，不会按阶段恢复执行。结束或取消不回滚已生成文件；未知的原生委派结果不会因重放同一次调用而再次执行。

当前关联由 `GET /api/office/group/execution` 的 `runTargets` 返回；员工 Run 查询补充 `projectId/workId/resumeSourceRunId`。群聊暂停、消息中的显式继续和员工 Run 取消/回答仍走既有接口。历史 Workflow 仅保留当前/按 ID 查询与退役 cancel，旧阶段 pause/resume 路由已移除。

派工与文件保护验证：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_office_pm_delegation.py backend/tests/test_office_workspace_operations.py backend/tests/test_execution_workspace.py backend/tests/test_office_stage2_worker.py -q
npm run test:minimum
npm run test:task-workspaces
```

上述 Worker 集成使用真实 PM 执行链与受控模型/员工 Executor，不把它当作真实模型项目交付。重启只按原生持久化 Run 恢复在途执行、从 Checkpoint 重建待答占用，不根据旧 Workflow 自动续跑；因此启动仍可能恢复原生在途工作。

桌面与真实 Gateway 联调：`npm run test:gateway-integration`。需要已安装后端 `.venv` 与桌面依赖；命令构建客户端，在临时目录启动独立 Gateway、SQLite 与受控模型，再启动实际 Electron 主进程及 preload，验证普通回答、ask/排队/刷新恢复、真实原生员工委派、暂停继续、取消及私聊。退出时停止测试进程，临时目录路径输出在日志中。生产模型、用户项目和生产数据不作为测试输入。

## macOS 桌面安装包

在 macOS 15+ 的 Apple Silicon 设备上完成源码环境准备后构建；构建入口要求原生 arm64 的 Node、Python 和 Electron。

```bash
npm run package:mac
```

本地测试包输出为 `dist/SmallOffice-<package.json版本>-mac-arm64-local.dmg`，采用 ad-hoc 签名，未经 Apple 公证。解包应用位于 `dist/mac-arm64/SmallOffice.app`；DMG 提供拖入 Applications 的安装入口。本地包可能被 Gatekeeper 拦截。

正式分发使用 `npm run package:mac:release`。构建环境需要可用的 Developer ID 签名身份和 Apple 公证凭据，可使用 `APPLE_KEYCHAIN_PROFILE`，或构建脚本支持的 Apple ID / App Store Connect API 凭据。成功时输出 `dist/SmallOffice-<package.json版本>-mac-arm64.dmg`。凭据只保存在本机或受保护的构建环境中。

Mac 包自带 Python 3.12、后端依赖、Node、Git 和 PowerShell。固定运行时及 SHA256 位于 `desktop/packaging/runtimes.macos.json`。构建后校验包内代码与资源、运行时可执行权限、arm64 架构和代码签名；正式分发模式还检查 Gatekeeper 评估及公证票据。

安装版配置、数据库和日志位于 `~/Library/Application Support/SmallOffice/office/`。资料库通过 Keychain 保护密钥。项目位于桌面、文稿、下载或外接磁盘时，应用会请求访问权限；访问被拒绝后，可在系统设置的“隐私与安全性 → 文件与文件夹”中检查应用授权。删除应用本体后，用户数据目录仍保留。

在 Mac 上用新建隔离用户目录运行实际应用检查首次配置：

```bash
node desktop/packaging/launch-smoke.cjs dist/mac-arm64/SmallOffice.app/Contents/MacOS/SmallOffice --fresh-profile
```

此检查使用本地受控模型。真实模型任务与受保护目录访问仍需在目标 Mac 上验证。

## Windows 桌面安装包

按上方固定环境安装根目录、Client 与后端开发依赖后运行 `npm run package:win`。构建入口位于 `desktop/packaging/build.cjs`，前端与打包器统一使用 Node 24.22.2；Electron 固定为 45.2.3。随包 Node、MinGit 和 PowerShell 的官方归档地址与 SHA256 固定在 `desktop/packaging/runtimes.json`，不查询 latest；Python 3.12.13 由固定版本 uv 的下载清单校验。Python 发行依赖从锁文件导出，通过 `uv pip sync --require-hashes` 安装，删除不属于当前依赖集的旧包。不要求开发者配置模型、搜索或资料库密钥，也不读取开发者运行数据库。首次构建会联网下载公开运行时和锁定依赖，目标电脑无需另装这些运行时。

输出为 `dist/SmallOffice-Setup-<package.json版本>-x64.exe`，支持选择安装目录、创建桌面和开始菜单快捷方式。包内包含 Electron、Python 3.12、后端依赖、Node、MinGit 和 PowerShell；用户无需单独安装这些运行时。安装版使用 `%APPDATA%/SmallOffice` 独立用户目录，配置、数据库和日志位于其 `office` 子目录，不使用仓库的开发数据。正常卸载会清空这个目录，包括历史对话、模型/API 配置、员工、记忆、自定义 Skills 和资料库；不删除绑定的项目目录。安装器内部替换版本不执行这项清理。自定义 `--user-data-dir` 测试目录不在卸载清理范围内。Python 后端、运行时和 Skill 资源使用 Windows 扩展路径，避免映射盘展开后超过普通路径长度限制而无法读取数据库迁移文件。

首次运行通过原有引导页面绑定工作区、填写公司名称、全局模型接口及 API Key。PM 初始提示词与技能直接使用当前源码模板，打包不再替换业务文件。发行包只携带仓库内无密钥配置模板。网页搜索仅通过 config.yaml 的 web_search 工具配置可选 api_key，前端不提供搜索设置。默认保留 fallback: ddgs；未配置密钥时直接使用 DDGS，配置密钥后沿用已有服务错误降级逻辑。修改配置文件后重启 Gateway 生效。资料库向量与精排密钥在资料库“模型配置”中填写，未配置时应用仍可启动。不携带开发者聊天模型密钥、搜索密钥、资料库密钥、会话、员工数据或上传资料。用户后续保存的配置不会在重启时被默认值覆盖。本开发阶段不自动迁移旧版数据，重置使用新版卸载程序清理。

`backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_office_release_defaults.py backend/tests/test_firecrawl_tools.py` 在临时目录验证无私人配置的发行准备、无密钥初始化、用户配置保留及搜索降级行为。

构建自动运行 `desktop/packaging/test_release.py`，检查独立初始化、配置保留、当前默认模板、运行设置，以及后端、Skill、桌面代码和前端构建文件的逐文件一致性；生成安装包后，`verify-package.cjs` 继续核对最终 `app.asar`、后端资源及运行时入口，避免仅暂存目录正确而发行包漏文件。

`node desktop/packaging/launch-smoke.cjs dist/win-unpacked/SmallOffice.exe --fresh-profile` 启动解包后的真实应用，用新建的隔离用户目录和本地受控模型检查首次配置；指定 `--session-dir=output/packaging/smoke-<时间戳>` 可复用该测试资料检查重启。脚本清除开发工具 PATH，模型服务随应用关闭退出；该测试目录中的可选 `model-responses.json` 仅用于编排受控模型的工具返回，不进入安装包。该验证不等同于干净 Windows 虚拟机或真实模型执行完整项目的验收。当前安装包未签名，使用 Electron 默认图标。

## 维护文档

- `CONTEXT.md`：唯一领域词汇表。
- `ARCHITECTURE.md`：当前架构与边界。
- `backend/UPSTREAM.md`：Agent 运行层来源和同步规则。
- `backend/docs/`：当前后端功能文档。

## 开源来源

办公室地图与 Phaser 可视化基于 SkyOffice 修改，许可见 [LICENSE](../LICENSE)。Agent 后端基于 DeerFlow 二次开发，来源和许可见 [UPSTREAM.md](../backend/UPSTREAM.md) 与 [DEERFLOW_LICENSE](../backend/DEERFLOW_LICENSE)。

# 测试与质量 / Testing & QA

> 测试与质量
>
> Testing & QA

## 能力说明

本技能包聚合了 **29 个子技能**，每个子技能都带完整的操作指南、脚本与模板，按需读取即可。

> **结构**：两级平铺。子技能指南为 `references/<子技能>.md`；配套脚本与数据在 `scripts/`、`assets/`、`templates/` 等目录下，以 `<子技能>--` 为前缀。

- `ai-regression-testing` —— AI 回归测试 —— 面向 AI 辅助开发的回归测试：沙箱模式 API 测试、bug-check 工作流与常见 AI 回归模式…
- `benchmark` —— 性能基准测试 —— 四模式性能基准：Core Web Vitals、API 延迟、构建耗时与前后对比。
- `browser-qa` —— 浏览器测试 —— 部署后用浏览器自动化做可视化测试与 UI 交互验证，四阶段流水线 + 默认只读安全。
- `codehealth-mcp` —— 代码健康度 —— 用 CodeScene MCP 做改前基线、改后增量校验与提交/PR 结构质量门禁。
- `cpp-testing` —— C++ 测试 —— C++17/20 的 GoogleTest/CTest 测试、覆盖率与 Sanitizer 实践。
- `csharp-testing` —— C# 测试 —— C#/.NET 的 xUnit、FluentAssertions、模拟与集成测试模式。
- `django-tdd` —— Django 测试驱动 —— Django/DRF 测试驱动开发：pytest-django、factory_boy、mock 与分层…
- `django-verification` —— Django验证 —— Django PR/部署前验证闭环：环境、lint、迁移、覆盖率、安全扫描与配置评审，附 CI 工作流。
- `e2e-testing` —— 端到端测试 —— Playwright 端到端测试：POM、CI 集成、不稳定测试与产物管理。
- `eval-harness` —— 评估驱动开发 —— 评估驱动开发框架：能力/回归评估、四类评分器、pass@k 指标与四阶段工作流。
- `fsharp-testing` —— F# 测试 —— xUnit、FsUnit、Unquote、FsCheck 的 F# 测试模式与集成测试、打桩与组织规范。
- `golang-testing` —— Go 测试 —— Go 表驱动测试、子测试、基准测试、模糊测试、覆盖率门禁与 CI 集成的完整测试手册。
- `healthcare-eval-harness` —— 医疗安全评估 —— 医疗应用部署的患者安全评估护栏：CDSS 准确性、PHI 泄漏、工作流完整性与集成合规的自动化测试门，失…
- `kotlin-testing` —— Kotlin 测试 —— Kotest、MockK、协程测试、属性测试与 Kover 覆盖率的 Kotlin 测试模式。
- `laravel-tdd` —— Laravel 测试 —— PHPUnit 与 Pest 的 Laravel TDD：工厂、HTTP 测试、鉴权、Mock 与覆盖率…
- `laravel-verification` —— Laravel 验证 —— Laravel 合并/上线前验证：环境、静态分析、测试覆盖率、安全与迁移、部署就绪。
- `perl-testing` —— Perl 测试 —— Test2::V0 断言、prove 跑测、Mock 隔离与 Devel::Cover 覆盖率，TDD…
- `plankton-code-quality` —— 代码质量管控 —— 通过 PostToolUse 钩子在每次编辑时自动格式化、检查并由 Claude 子进程分层修复代码违规…
- `python-testing` —— Python 测试 —— pytest 全流程测试：TDD、fixture、参数化、mock、异步与覆盖率门禁。
- `quarkus-tdd` —— Quarkus 测试驱动 —— Quarkus 3.x TDD：JUnit 5、Mockito、Camel 路由与 JaCoCo 门禁。
- `quarkus-verification` —— Quarkus 验证 —— Quarkus 项目十阶段验证闭环：构建、静态分析、测试覆盖率、安全扫描、原生编译、压测、健康检查与容器…
- `react-testing` —— React 测试 —— React 组件测试：RTL、Vitest/Jest、MSW、axe 无障碍与测试边界。
- `repo-scan` —— 仓库盘点 —— 跨栈源码资产盘点：文件归类、第三方库识别、四级处置结论与 HTML 报告。
- `rust-testing` —— Rust 测试 —— Rust 测试：TDD 红绿重构、单元/集成/异步测试、proptest 属性测试、mockall 打桩…
- `springboot-tdd` —— Spring 测试驱动 —— Spring Boot 测试驱动：JUnit 5、Mockito、Testcontainers 与 Ja…
- `springboot-verification` —— SpringBoot验证 —— Spring Boot 项目的验证闭环：构建、静态分析、带覆盖率的测试、安全扫描与合并前 diff 评审…
- `swift-protocol-di-testing` —— Swift 协议注入测试 —— 用聚焦协议抽象文件/网络/API 依赖，默认注入生产实现、测试注入 mock，使 Swift Testi…
- `tdd-workflow` —— TDD 工作流 —— 强制测试驱动开发，单元/集成/E2E 覆盖 80%+，含 RED/GREEN 循环、覆盖率校验与 TDD…
- `verification-loop` —— 验证闭环 —— 六阶段验证闭环：构建、类型、静态检查、测试、密钥扫描与 diff 复审。

## 何时使用

- 「给这个模块补一套 TDD 测试，先写失败用例再实现」
- 「部署后跑一次浏览器 QA，检查控制台错误和界面回归」
- 「把接口性能做成基准测试，给出优化前后的对比数据」

**不适用**：目标产物是 `.docx` / `.xlsx` / `.pptx` / `.pdf` 文档本身——那一步交给「文档格式分流」里的已上架技能；或与上述子技能无关的领域问题。

## 文档格式分流

| 目标格式 | 使用技能（name） | 展示名 |
|---|---|---|
| Word `.docx` / `.dotx` | `docx-pro` | Word 文档处理 |
| Excel `.xlsx` | `xlsx` | Excel 表格处理 |
| PPT `.pptx` / `.potx` | `pptx` | PPT 演示文稿 |
| PDF `.pdf` | `pdf-pro` | PDF 文档处理 |

未安装时会先自动安装再调用，**但只认同名精确匹配**：按完整 `name` 搜索后逐项核对 —— `name` 逐字符完全一致 + 展示名一致 + author 同源，四项全中才装。恰好 1 条命中→直接装；0 条命中→不装近似名技能并告知你；≥2 条命中→把候选列给你选，不擅自安装。

---

## English

> Testing & QA

This package bundles **29 sub-skills**, each with its own complete guide, scripts and templates. Read them on demand from `references/<sub-skill>.md`.

> **Layout**: two levels only. Each sub-skill guide lives at `references/<sub-skill>.md`; its scripts and data live under `scripts/`, `assets/`, `templates/`, prefixed with `<sub-skill>--`. Every relative path resolves against the package root.

### Format routing & related skills

This package covers the engineering work itself. Route Office/PDF deliverables by `name` — `docx-pro` (Word), `xlsx` (Excel), `pptx` (PowerPoint), `pdf-pro` (PDF). Missing ones are installed automatically, but **only on an exact name match** — the `name` must match character-for-character, with the same display name and author. One hit → install; zero hits → no look-alike is installed; two or more → candidates are listed for the user to choose.

## 版本

- `1.0.0` —— 首个版本：由 skills090522 技能集（286 个技能）重新分类聚合为 8 个技能包，本包含 29 个子技能，补齐中英双语入口、调用约定与交付校验清单；采用两级平铺结构以适配开放平台目录限制。

## 来源与许可

子技能内容整理自既有开源技能集（原子技能 frontmatter 标注 `origin: ECC`），由 `zxh` 重新分类、聚合并适配 WorkBuddy 开放平台技能规范。

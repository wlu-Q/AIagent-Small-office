---
name: testing-quality
display_name: "测试与质量"
display_name_en: "Testing & QA"
description: >-
  Testing and QA (29 sub-skills): TDD workflows, verification loops, e2e and browser QA, benchmarks, AI regression testing and eval harnesses for Python, Go, Rust and Swift.
  测试与质量（29 个子技能）：TDD 工作流与验证循环、端到端与浏览器 QA、性能基准（Core Web Vitals/API/构建）、AI 回归测试与评测框架，并覆盖 Python、React、Go、Rust、C++、C#、F#、Kotlin、Perl、Swift 及 Django/Laravel/Quarkus/Spring Boot 的多语言测试闭环。
  触发词 / Trigger words：测试、TDD、单元测试、端到端、E2E、回归测试、性能基准、覆盖率、浏览器测试, testing, tdd, e2e, benchmark, regression test, code quality
description_zh: "测试与质量（29 个子技能）：TDD 工作流与验证循环、端到端与浏览器 QA、性能基准（Core Web Vitals/API/构建）、AI 回归测试与评测框架，并覆盖 Python、React、Go、Rust、C++、C#、F#、Kotlin、Perl、Swift 及 Django/Laravel/Quarkus/Spring Boot 的多语言测试闭环。"
description_en: "Testing and QA (29 sub-skills): TDD workflows, verification loops, e2e and browser QA, benchmarks, AI regression testing and eval harnesses for Python, Go, Rust and Swift."
category: testing
version: "1.0.0"
author: "zxh"
disable-model-invocation: false
user-invocable: true
runtime: generic
title: "测试与质量 / Testing & QA"
tags: ["测试", "TDD", "E2E", "回归测试", "性能基准", "代码质量", "QA", "覆盖率", "验证"]
examples_zh:
  - "给这个模块补一套 TDD 测试，先写失败用例再实现"
  - "部署后跑一次浏览器 QA，检查控制台错误和界面回归"
  - "把接口性能做成基准测试，给出优化前后的对比数据"
examples_en:
  - "Write a TDD suite for this module — failing tests first, then the implementation"
  - "Run browser QA after deploy and check console errors and UI regressions"
  - "Benchmark these API endpoints and show before/after numbers"
---

# 测试与质量 / Testing & QA

> **语言路由**：中文输入 → 全程中文输出；其他语言 → 优先英文输出。库名、命令、API 名保留英文原文。

> **路径约定**：本技能包为**两级平铺结构**。子技能指南在 `references/<子技能>.md`；配套资源在包根的 `references/`、`scripts/`、`assets/`、`templates/` 等目录下，文件名以 `<子技能>--` 为前缀。**所有相对路径均相对于技能包根目录。**

## 何时使用

当用户的需求命中以下任一情形时使用本技能（中文示例）：

- 「给这个模块补一套 TDD 测试，先写失败用例再实现」
- 「部署后跑一次浏览器 QA，检查控制台错误和界面回归」
- 「把接口性能做成基准测试，给出优化前后的对比数据」

英文对照：

- "Write a TDD suite for this module — failing tests first, then the implementation"
- "Run browser QA after deploy and check console errors and UI regressions"
- "Benchmark these API endpoints and show before/after numbers"

**不适用：**

- 目标产物是 `.docx` / `.xlsx` / `.pptx` / `.pdf` 文档本身 → 见文末「文档格式分流」，转交已上架技能。
- 纯粹的闲聊、翻译、与本节子技能无关的领域问题 → 交给对应的兄弟技能包。
- 需要真实凭证、真实生产数据或不可逆操作时，先向用户确认，不要自行执行。

## 包含的子技能（29 个）

本包是一个**聚合技能**：下面每个子技能都有独立、完整的操作指南与脚本，需要时按需读取。

| 子技能 | 用途 | 详细文档 |
|---|---|---|
| `ai-regression-testing` | AI 回归测试 —— 面向 AI 辅助开发的回归测试：沙箱模式 API 测试、bug-check 工作流与常见 AI 回归模式… | `references/ai-regression-testing.md` |
| `benchmark` | 性能基准测试 —— 四模式性能基准：Core Web Vitals、API 延迟、构建耗时与前后对比。 | `references/benchmark.md` |
| `browser-qa` | 浏览器测试 —— 部署后用浏览器自动化做可视化测试与 UI 交互验证，四阶段流水线 + 默认只读安全。 | `references/browser-qa.md` |
| `codehealth-mcp` | 代码健康度 —— 用 CodeScene MCP 做改前基线、改后增量校验与提交/PR 结构质量门禁。 | `references/codehealth-mcp.md` |
| `cpp-testing` | C++ 测试 —— C++17/20 的 GoogleTest/CTest 测试、覆盖率与 Sanitizer 实践。 | `references/cpp-testing.md` |
| `csharp-testing` | C# 测试 —— C#/.NET 的 xUnit、FluentAssertions、模拟与集成测试模式。 | `references/csharp-testing.md` |
| `django-tdd` | Django 测试驱动 —— Django/DRF 测试驱动开发：pytest-django、factory_boy、mock 与分层… | `references/django-tdd.md` |
| `django-verification` | Django验证 —— Django PR/部署前验证闭环：环境、lint、迁移、覆盖率、安全扫描与配置评审，附 CI 工作流。 | `references/django-verification.md` |
| `e2e-testing` | 端到端测试 —— Playwright 端到端测试：POM、CI 集成、不稳定测试与产物管理。 | `references/e2e-testing.md` |
| `eval-harness` | 评估驱动开发 —— 评估驱动开发框架：能力/回归评估、四类评分器、pass@k 指标与四阶段工作流。 | `references/eval-harness.md` |
| `fsharp-testing` | F# 测试 —— xUnit、FsUnit、Unquote、FsCheck 的 F# 测试模式与集成测试、打桩与组织规范。 | `references/fsharp-testing.md` |
| `golang-testing` | Go 测试 —— Go 表驱动测试、子测试、基准测试、模糊测试、覆盖率门禁与 CI 集成的完整测试手册。 | `references/golang-testing.md` |
| `healthcare-eval-harness` | 医疗安全评估 —— 医疗应用部署的患者安全评估护栏：CDSS 准确性、PHI 泄漏、工作流完整性与集成合规的自动化测试门，失… | `references/healthcare-eval-harness.md` |
| `kotlin-testing` | Kotlin 测试 —— Kotest、MockK、协程测试、属性测试与 Kover 覆盖率的 Kotlin 测试模式。 | `references/kotlin-testing.md` |
| `laravel-tdd` | Laravel 测试 —— PHPUnit 与 Pest 的 Laravel TDD：工厂、HTTP 测试、鉴权、Mock 与覆盖率… | `references/laravel-tdd.md` |
| `laravel-verification` | Laravel 验证 —— Laravel 合并/上线前验证：环境、静态分析、测试覆盖率、安全与迁移、部署就绪。 | `references/laravel-verification.md` |
| `perl-testing` | Perl 测试 —— Test2::V0 断言、prove 跑测、Mock 隔离与 Devel::Cover 覆盖率，TDD… | `references/perl-testing.md` |
| `plankton-code-quality` | 代码质量管控 —— 通过 PostToolUse 钩子在每次编辑时自动格式化、检查并由 Claude 子进程分层修复代码违规… | `references/plankton-code-quality.md` |
| `python-testing` | Python 测试 —— pytest 全流程测试：TDD、fixture、参数化、mock、异步与覆盖率门禁。 | `references/python-testing.md` |
| `quarkus-tdd` | Quarkus 测试驱动 —— Quarkus 3.x TDD：JUnit 5、Mockito、Camel 路由与 JaCoCo 门禁。 | `references/quarkus-tdd.md` |
| `quarkus-verification` | Quarkus 验证 —— Quarkus 项目十阶段验证闭环：构建、静态分析、测试覆盖率、安全扫描、原生编译、压测、健康检查与容器… | `references/quarkus-verification.md` |
| `react-testing` | React 测试 —— React 组件测试：RTL、Vitest/Jest、MSW、axe 无障碍与测试边界。 | `references/react-testing.md` |
| `repo-scan` | 仓库盘点 —— 跨栈源码资产盘点：文件归类、第三方库识别、四级处置结论与 HTML 报告。 | `references/repo-scan.md` |
| `rust-testing` | Rust 测试 —— Rust 测试：TDD 红绿重构、单元/集成/异步测试、proptest 属性测试、mockall 打桩… | `references/rust-testing.md` |
| `springboot-tdd` | Spring 测试驱动 —— Spring Boot 测试驱动：JUnit 5、Mockito、Testcontainers 与 Ja… | `references/springboot-tdd.md` |
| `springboot-verification` | SpringBoot验证 —— Spring Boot 项目的验证闭环：构建、静态分析、带覆盖率的测试、安全扫描与合并前 diff 评审… | `references/springboot-verification.md` |
| `swift-protocol-di-testing` | Swift 协议注入测试 —— 用聚焦协议抽象文件/网络/API 依赖，默认注入生产实现、测试注入 mock，使 Swift Testi… | `references/swift-protocol-di-testing.md` |
| `tdd-workflow` | TDD 工作流 —— 强制测试驱动开发，单元/集成/E2E 覆盖 80%+，含 RED/GREEN 循环、覆盖率校验与 TDD… | `references/tdd-workflow.md` |
| `verification-loop` | 验证闭环 —— 六阶段验证闭环：构建、类型、静态检查、测试、密钥扫描与 diff 复审。 | `references/verification-loop.md` |

## 工作方式

1. **先选子技能**：读上面的索引表，判断用户需求落在哪一个（或哪几个）子技能上。
2. **再读全文**：读取 `references/<子技能>.md` 的完整内容，**按其步骤执行**，不要仅凭标题猜测做法。
3. **同包找配套资源**：子技能自带的脚本、模板与示例已平铺到包根 —— 分别为 `scripts/<子技能>--…`、`assets/<子技能>--…`、`templates/<子技能>--…`。**所有相对路径以技能包根目录为基准**（子技能指南开头也写明了这一点）。
4. **可串联**：多个子技能常需接力，例如「代码导览 → 编排加功能 → 测试与验证」或「安全审计 → 修复 → 回归」。串联时逐步确认中间产出。
5. **动手前先确认边界**：涉及破坏性命令、生产环境变更、凭证与付费接口时，必须先向用户说明影响并取得确认；子技能里标注的「只读」「沙箱」「需人工复核」等限制必须原样保留。
6. **不编造**：子技能未覆盖的 API、参数、版本号，先查证再回答；查不到就直说。

## 交付前校验清单

- 关键命令、配置片段与代码块都实际可运行或有明确来源，不是凭印象拼的。
- 破坏性操作（删库、改生产配置、强推、覆盖文件）已标注风险并给出回滚方式。
- 涉及版本、框架、云服务的建议写清了适用的版本区间与前置条件。
- 交付物里的路径、端口、环境变量与实际仓库一致，没有残留占位符。
- 涉及安全、合规、支付、医疗等敏感场景时，适用范围与限制已写明。

## 文档格式分流

本技能负责工程实现本身。**目标产物是 Office / PDF 文档时，转交下表对应技能**：

| 目标格式 | 使用技能（name） | 展示名 |
|---|---|---|
| Word `.docx` / `.dotx` | `docx-pro` | Word 文档处理 |
| Excel `.xlsx` | `xlsx` | Excel 表格处理 |
| PPT `.pptx` / `.potx` | `pptx` | PPT 演示文稿 |
| PDF `.pdf` | `pdf-pro` | PDF 文档处理 |

补充：Markdown 源文件的语法与渲染问题交给 `markdown-md`。

## 调用流程（先按 `name` 精确匹配 → 未安装才装 → 再调用）

同族技能，需要时可直接调用；若当前 WorkBuddy 未安装，先按 `name` 在技能市场搜索并自动安装，再调用（安装一次后持久复用）。

> ⚠️ **按 `name` 搜索会返回大量近似结果**，必须先做**同名精确匹配核对**再安装，绝不允许按相似度猜。

1. **查** —— 先用 `skill_management` 列出已安装技能，或检查 `~/.workbuddy/skills/<name>/SKILL.md`；**已装 → 直接调用，不必搜索**。
2. **搜** —— 未装才搜：`search_and_install_skills`，关键词用**完整 name** 或上表展示名。
3. **核（关键）** —— 逐项核对，**四项全中方为命中**：`name` **逐字符完全相同**（kebab-case 一字不差；前缀/后缀/子串/近拼写一律排除）· `display_name` / `display_name_en` 与上表一致 · `author` 同为 `zxh` · 条目类型是技能（skill）。
4. **分流** —— **恰好 1 条命中** → 安装后调用；**0 条命中** → **不安装任何近似名技能**，告知用户未找到并请其确认，**不得用同类技能顶替**；**≥2 条命中** → **不自动安装**，把候选（`name` + 展示名 + author + 版本）列给用户指定。
5. **兜底** —— 平台要求确认安装或授权时，先向用户说明再继续，**不得静默跳过或降级输出**。

## 维护说明

- 本技能是**纯指令集**：内容为文档与脚本，不上传用户数据、不写入外部系统。
- 子技能脚本默认要求 Python 3.11+；需要联网、需要凭证或需要 GPU 的子技能已在各自指南里声明。
- 使用中发现新的踩坑场景，请补充进对应子技能的 `references/<子技能>.md` 并递增 `version` 后再发布。

## 来源与许可

子技能内容整理自既有开源技能集（原子技能 frontmatter 标注 `origin: ECC`），由 `zxh` 重新分类、聚合并补写中英双语入口与调用约定，适配 WorkBuddy 开放平台技能规范。

# Contributing · 参与贡献

[English](#english) · [简体中文](#简体中文)

## English

Thanks for helping improve Small Office.

1. Read [AGENTS.md](AGENTS.md) and the [domain glossary](CONTEXT.md). Use the pinned Windows x64 toolchain and setup commands in [README.md](README.md).
2. Keep changes focused on the problem. Prefer existing Git and platform capabilities, and avoid speculative compatibility layers or fallback paths.
3. Keep API keys, personal configuration, conversations, runtime data, and generated installers out of changes. Tests should use isolated data and local model fixtures.
4. Describe the problem, the resulting behavior, and how you verified it. For interface changes, include a screenshot and check both English and Simplified Chinese.

After setup, useful checks from the repository root are:

```powershell
node --test desktop/*.test.cjs client/tests/employee-interaction.test.cjs client/tests/employee-pickup.test.cjs
npm run test:i18n
npm run test:minimum
npm run test:gateway-integration
npm run test:task-workspaces
npm run test:terminal
npm run test:delivery
```

The Windows CI workflow checks startup from a fresh checkout, bilingual desktop rendering, first-time setup, and the Office backend suite. Run the full Electron and Gateway checks above locally when changing employee collaboration, Git workspaces, terminal approvals, or file delivery. Both use controlled local models. [Windows CI workflow](.github/workflows/ci.yml)

Detailed packaging and maintenance instructions are available in [the Chinese development guide](docs/development.zh-CN.md). Contributions use the project's [MIT License](LICENSE).

## 简体中文

欢迎一起改进小小办公室。

1. 先阅读 [AGENTS.md](AGENTS.md) 和 [领域词汇](CONTEXT.md)，按照 [中文介绍](README.zh-CN.md) 使用固定的 Windows x64 工具链完成环境准备。
2. 围绕具体问题提交改动。优先复用已有 Git 和平台能力，避免为尚未发生的问题增加兼容层或降级分支。
3. 密钥、个人配置、对话、运行数据和生成的安装包不进入变更。测试使用隔离数据与本地受控模型。
4. 说明解决的问题、修改后的行为和验证方式。界面改动附截图，并检查英文和简体中文。

完成环境准备后，可以在仓库根目录执行：

```powershell
node --test desktop/*.test.cjs client/tests/employee-interaction.test.cjs client/tests/employee-pickup.test.cjs
npm run test:i18n
npm run test:minimum
npm run test:gateway-integration
npm run test:task-workspaces
npm run test:terminal
npm run test:delivery
```

Windows CI 检查干净检出后的启动环境、双语桌面、首次配置和 Office 后端。修改员工协作、Git 工作区、终端审批或文件交付时，在本机运行上面的完整 Electron 与 Gateway 联调。两处验证均使用本地受控模型。[Windows CI](.github/workflows/ci.yml)

打包和维护操作见 [运行与维护说明](docs/development.zh-CN.md)。贡献内容遵循项目的 [MIT 许可](LICENSE)。

# 本地启动

## 1. 环境

- Python 3.12+
- `uv`
- Node.js 与 npm（启动桌面时需要）

## 2. 配置

在仓库根目录：

```powershell
Copy-Item config.example.yaml config.yaml
Copy-Item extensions_config.example.json extensions_config.json
```

至少配置一个可用模型。真实密钥使用环境变量，不提交到 Git。

可选路径环境变量：

- `DEER_FLOW_PROJECT_ROOT`：项目根目录；
- `DEER_FLOW_CONFIG_PATH`：主配置路径；
- `DEER_FLOW_HOME`：本地数据目录。

## 3. 安装依赖

```powershell
Set-Location backend
uv sync
Set-Location ..
```

默认 LocalSandbox 不要求 Docker。只有显式选择容器或远程 Sandbox 时才需要对应运行环境。

## 4. 启动 Gateway

从仓库根目录：

```powershell
npm run deerflow:gateway
```

默认地址：`http://127.0.0.1:8001`。

也可以在 `backend/` 直接运行：

```powershell
uv run --no-editable uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
```

## 5. 基础检查

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8001/api/models
Invoke-RestMethod http://127.0.0.1:8001/api/office/project
```

如果启用了认证，除健康检查外的请求需要有效会话。

## 6. 启动桌面

```powershell
npm install
Set-Location client
npm install
npm run build
Set-Location ..
npm run desktop:start
```

开发模式：

```powershell
npm run desktop:client
npm run desktop:start:dev
```

## 7. 常见问题

- 配置未找到：确认命令从仓库根目录运行，或设置显式路径环境变量。
- 8001 端口占用：停止已有 Gateway，不要启动并行副本。
- 模型不可用：检查 `config.yaml` 模型和密钥环境变量。
- Office 项目为空：这是未绑定项目的正常响应，可在桌面项目面板选择目录。
- 非模型 API 可启动不代表真实 Agent Run 已验证。
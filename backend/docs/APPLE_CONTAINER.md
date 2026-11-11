# AIO Sandbox 可选容器适配器

小小办公室默认使用本地 `LocalSandboxProvider`，不需要 Docker 或 Apple Container。本页只服务于主动选择 `AioSandboxProvider` 的高级用户，不能作为默认安装步骤。

## 适用边界

在 `config.yaml` 中显式设置以下配置后，Gateway 才会启用 AIO Sandbox：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  image: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

实现位于 `backend/packages/harness/deerflow/community/aio_sandbox/`。`LocalContainerBackend` 在 macOS 上优先检测 Apple Container，检测失败或在其他平台运行时使用 Docker。

## 运行时要求

- Apple Container：仅适用于满足其系统要求的 macOS 与 Apple Silicon 环境。
- Docker：适用于已安装并启动 Docker Engine 或 Docker Desktop 的环境。
- 镜像必须兼容当前 CPU 架构。

代码不会通过项目 Makefile 安装、拉取或清理容器运行时。是否启用、镜像拉取和容器管理都由用户显式负责。

## 手工准备镜像

Apple Container：

```bash
container image pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

Docker：

```bash
docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

## 验证实现

不启动真实容器也可以验证运行时选择和命令组装逻辑：

```powershell
Set-Location backend
uv run --no-editable pytest -q tests/test_aio_sandbox_local_backend.py
```

真实容器验收必须由启用该适配器的环境单独执行，不属于默认本地单用户冷启动门。

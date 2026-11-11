@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo 未找到 Node.js，请安装 Node.js 后重试。
  pause
  exit /b 1
)
rem --detach: desktop app runs on its own, this window can be closed safely.
rem exit code 2 = already running, 1 = startup failed (batch errorlevel means ">=").
node desktop\dev.cjs --detach
if errorlevel 2 (
  echo.
  echo 小小办公室已经在运行，请看任务栏里已有的窗口。
  ping -n 5 127.0.0.1 >nul
  exit /b 0
)
if errorlevel 1 (
  echo.
  echo 启动失败，请查看上方提示和 output\logs\development.log。
  pause
  exit /b 1
)
endlocal

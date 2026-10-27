@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   stockDisplay 启动中...
echo ============================================================
echo.

REM 检查 conda 是否可用
where conda >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 conda 命令。
    echo 请先运行一次: conda init cmd.exe
    echo 然后关闭并重新打开终端再试。
    pause
    exit /b 1
)

REM 用 stock 环境运行；--no-capture-output 保证日志实时输出
conda run --no-capture-output -n stock python serve.py %*

if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请检查上方输出。
    pause
)
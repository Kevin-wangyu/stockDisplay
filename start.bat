@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   stockDisplay 启动中...
echo ============================================================
echo.

REM 动态查询 conda 环境路径（只查一次，不进 conda run）
for /f "delims=" %%i in ('conda info --base 2^>nul') do set CONDA_BASE=%%i

if not defined CONDA_BASE (
    echo [错误] 未找到 conda，请运行一次: conda init cmd.exe
    pause
    exit /b 1
)

set PYTHON_EXE=%CONDA_BASE%\envs\stock\python.exe

if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到 conda 环境 stock: %PYTHON_EXE%
    echo 请先创建: conda create -n stock python=3.11
    pause
    exit /b 1
)

"%PYTHON_EXE%" serve.py %*

if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请检查上方输出。
    pause
)
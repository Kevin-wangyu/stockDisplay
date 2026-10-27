@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ============================================================
echo   stockDisplay 启动中...
echo ============================================================
echo.
set PYTHON_EXE=D:\Softwares\miniconda3\envs\stock\python.exe
set "HTMLFILE="

"%PYTHON_EXE%" serve.py %*
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请检查上方输出。
    pause
)
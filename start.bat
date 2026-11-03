@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   stockDisplay 启动中...
echo ============================================================
echo.

set "PYTHON_EXE="

REM ═══ 1. 优先读 .python_path（本机缓存）═══
if exist ".python_path" (
    set /p PYTHON_EXE=<.python_path
    if not exist "!PYTHON_EXE!" set "PYTHON_EXE="
)

REM ═══ 2. 当前已激活的 stock 环境（%CONDA_PREFIX%）═══
if not defined PYTHON_EXE (
    if defined CONDA_PREFIX (
        if exist "!CONDA_PREFIX!\python.exe" (
            echo !CONDA_PREFIX! | findstr /I "\\envs\\stock" >nul
            if not errorlevel 1 (
                set "PYTHON_EXE=!CONDA_PREFIX!\python.exe"
            )
            echo !CONDA_PREFIX! | findstr /I /E "\\stock" >nul
            if not errorlevel 1 (
                set "PYTHON_EXE=!CONDA_PREFIX!\python.exe"
            )
        )
    )
)

REM ═══ 3. conda env list 解析 stock 行 ═══
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('conda env list 2^>nul ^| findstr /R /C:"\<stock\>"') do (
        for /f "tokens=1,* delims= " %%a in ("%%i") do (
            if /I "%%a"=="stock" (
                if exist "%%b\python.exe" (
                    set "PYTHON_EXE=%%b\python.exe"
                )
            )
        )
    )
)

REM ═══ 4. conda info --base\envs\stock ═══
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('conda info --base 2^>nul') do (
        if exist "%%i\envs\stock\python.exe" (
            set "PYTHON_EXE=%%i\envs\stock\python.exe"
        )
    )
)

REM ═══ 5. %USERPROFILE%\.conda\envs\stock（用户目录下）═══
if not defined PYTHON_EXE (
    if exist "%USERPROFILE%\.conda\envs\stock\python.exe" (
        set "PYTHON_EXE=%USERPROFILE%\.conda\envs\stock\python.exe"
    )
)

REM ═══ 6. 全部失败 → 提示 ═══
if not defined PYTHON_EXE (
    echo [错误] 未找到 conda 环境 stock 的 python.exe
    echo.
    echo 请确认 conda 环境已创建：
    echo     conda env list
    echo.
    echo 若不存在，创建：
    echo     conda create -n stock python=3.11 -y
    echo     conda activate stock
    echo     pip install -r requirements.txt
    echo.
    echo 若已存在但自动探测失败，请手动创建 .python_path 文件，
    echo 内容为 python.exe 的完整路径（一行），例如：
    echo     C:\Users\你的用户名\.conda\envs\stock\python.exe
    pause
    exit /b 1
)

REM ═══ 缓存路径（下次启动秒开）═══
echo !PYTHON_EXE!>.python_path
echo [启动] Python: !PYTHON_EXE!
echo.

"!PYTHON_EXE!" serve.py %*

if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请检查上方输出。
    pause
)

endlocal
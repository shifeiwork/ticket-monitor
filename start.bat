@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

:: ============================================================
:: 12306 余票监测 - 一键启动脚本
:: 用法：双击 start.bat 即可，无需手动配置 Python 路径
:: ============================================================

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ============================================
echo   12306 余票监测服务
echo   工作目录：%SCRIPT_DIR%
echo ============================================
echo.

:: ---------- 1. 查找 Python ----------
set "PY="
:: 优先使用 PATH 中的 python / python3
for %%P in (python python3) do (
    if not defined PY (
        where /q %%P 2>nul
        if !errorlevel! equ 0 set "PY=%%P"
    )
)
:: 兜底：扫描常见安装目录
if not defined PY (
    for %%D in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%PROGRAMFILES%\Python313\python.exe"
        "%PROGRAMFILES%\Python312\python.exe"
        "%PROGRAMFILES%\Python311\python.exe"
        "%PROGRAMFILES(x86)%\Python313\python.exe"
    ) do (
        if not defined PY (
            if exist %%D set "PY=%%D"
        )
    )
)

if not defined PY (
    echo [错误] 未找到 Python，请安装 Python 3.8 以上版本后重试。
    echo        下载地址：https://www.python.org/
    pause
    exit /b 1
)

echo [信息] 使用 Python："%PY%"
"%PY%" --version
if !errorlevel! neq 0 (
    echo [错误] Python 版本检测失败
    pause
    exit /b 1
)
echo.

:: ---------- 2. 确保 requests 已安装 ----------
"%PY%" -c "import requests" 2>nul
if !errorlevel! neq 0 (
    echo [信息] 正在安装依赖 requests ...
    "%PY%" -m pip install --quiet requests
    if !errorlevel! neq 0 (
        echo [错误] 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
    echo [信息] 依赖安装完成
    echo.
) else (
    echo [信息] requests 已就绪
)

:: ---------- 3. 启动服务 ----------
echo [信息] 正在启动服务 ...
echo [提示] 浏览器打开：http://127.0.0.1:8766
echo [提示] 关闭此窗口即可停止服务
echo ============================================
echo.

"%PY%" server.py
set "EXIT_CODE=%errorlevel%"

if !EXIT_CODE! neq 0 (
    echo.
    echo [错误] 服务异常退出，退出码：!EXIT_CODE!
    pause
)
exit /b !EXIT_CODE!

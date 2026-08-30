@echo off
REM Opens the validator window straight from the source, without building an
REM .exe. Useful when Smart App Control blocks unsigned executables, or to try
REM the tool before packaging it. Needs Python 3.8+ on this machine; no other
REM packages are required.
setlocal

pushd "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    python3 --version >nul 2>&1 && set "PY=python3"
)

if not defined PY (
    echo [ERROR] Python was not found. Install Python 3.8 or newer from
    echo https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    echo.
    popd
    pause >nul
    exit /b 1
)

%PY% main.py %*
set "CODE=%ERRORLEVEL%"

popd
if not "%CODE%"=="0" (
    echo.
    echo Exited with code %CODE%. Press any key to close.
    pause >nul
)
exit /b %CODE%

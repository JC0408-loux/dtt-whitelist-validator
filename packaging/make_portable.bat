@echo off
REM ---------------------------------------------------------------------------
REM Builds the portable folder. No Python needs to be installed anywhere -
REM the interpreter is downloaded as an archive and unpacked into the output.
REM
REM Only this machine needs internet access.
REM ---------------------------------------------------------------------------
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_portable.ps1" %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
    echo Build finished. Press any key to close.
) else (
    echo Build failed with code %CODE%. The message above says why.
    echo Press any key to close.
)
pause >nul
exit /b %CODE%

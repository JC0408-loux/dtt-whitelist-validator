@echo off
REM ---------------------------------------------------------------------------
REM Friendly entry point for building the portable version.
REM
REM Only this machine needs internet access and it does not need Python:
REM the interpreter is downloaded as an archive and unpacked into the output.
REM The result runs on any Windows machine with neither.
REM
REM This is a wrapper. The build itself lives in packaging\make_portable.ps1.
REM ---------------------------------------------------------------------------
setlocal

echo ========================================
echo DTT Whitelist Validator - Build Portable Version
echo ========================================
echo.
echo This will:
echo   1. Download a self-contained Python interpreter
echo   2. Copy the application and install openpyxl
echo   3. Verify tkinter actually works in it
echo   4. Write the 'portable' folder and a distributable ZIP
echo.
echo Press any key to continue, or Ctrl+C to cancel...
pause >nul

cd /d "%~dp0"

REM The .ps1 directly rather than packaging\make_portable.bat: that wrapper
REM pauses on its own, and two prompts in a row reads like the build stalled.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\make_portable.ps1" %*
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
    echo ========================================
    echo Build FAILED with code %CODE%.
    echo The message above says why.
    echo ========================================
    echo Press any key to close.
    pause >nul
    exit /b %CODE%
)

echo ========================================
echo Build completed successfully.
echo ========================================
echo.
echo   Portable folder: portable\
echo   ZIP file:        see the path printed above
echo.
echo Copy the folder, or hand out the ZIP. The target machine needs neither
echo Python nor an internet connection - but whoever downloads the ZIP must
echo right-click it, Properties, Unblock, before extracting.
echo.
echo Press any key to close.
pause >nul
exit /b 0

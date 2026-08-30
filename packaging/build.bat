@echo off
REM ---------------------------------------------------------------------------
REM Builds dist\dtt-wl-validator.exe.
REM
REM Run this on a machine WITH internet access and Python installed. The
REM resulting .exe is self-contained: the test machine needs no Python and no
REM network.
REM
REM Every path below stops with an explanation and waits, so nothing is lost
REM when the window is opened by double-click.
REM ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion

pushd "%~dp0.."
echo Working directory: %CD%
echo.

REM --- locate Python ---------------------------------------------------------
REM `if not defined PY cmd && set ...` would run the set even when PY is
REM already defined, because && tests the if statement itself. Use blocks.
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    python3 --version >nul 2>&1 && set "PY=python3"
)

if not defined PY (
    echo [ERROR] Python was not found on this machine.
    echo.
    echo Install Python 3.8 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then run this again.
    echo.
    echo This is only needed on the BUILD machine. The .exe it produces runs
    echo on the test machine with no Python and no network.
    goto :finish_error
)

for /f "delims=" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo Using %PY%  (!PYVER!)
echo.

REM --- build dependencies ----------------------------------------------------
echo === Installing build dependencies (needs internet) ===
%PY% -m pip install --upgrade pyinstaller openpyxl
if errorlevel 1 (
    echo.
    echo [ERROR] Could not install PyInstaller.
    echo Check that this machine has internet access, or that pip is allowed
    echo through the proxy. The test machine does not need any of this.
    goto :finish_error
)
echo.

REM --- tests -----------------------------------------------------------------
if /i "%~1"=="--skip-tests" (
    echo === Skipping tests as requested ===
) else (
    echo === Running tests ===
    %PY% -m unittest discover -s tests -t .
    if errorlevel 1 (
        echo.
        echo [ERROR] Tests failed. Fix these before building, or rerun as
        echo         build.bat --skip-tests to build anyway.
        goto :finish_error
    )
)
echo.

REM --- build -----------------------------------------------------------------
echo === Building dist\dtt-wl-validator.exe ===
%PY% -m PyInstaller --clean --noconfirm packaging\dtt_wl_validator.spec
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed. The output above says why.
    goto :finish_error
)

if not exist "dist\dtt-wl-validator.exe" (
    echo.
    echo [ERROR] PyInstaller reported success but dist\dtt-wl-validator.exe
    echo         is missing. Antivirus may have quarantined it.
    goto :finish_error
)

echo.
echo ===========================================================
echo  Build complete:  %CD%\dist\dtt-wl-validator.exe
echo ===========================================================
echo.
echo Copy that one file to the test machine and run it. Nothing else is
echo needed there - no Python, no network, no install.
echo.
popd
echo Press any key to close.
pause >nul
exit /b 0

:finish_error
echo.
popd
echo Build did not complete. Press any key to close.
pause >nul
exit /b 1

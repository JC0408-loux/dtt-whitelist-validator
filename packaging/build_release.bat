@echo off
REM ---------------------------------------------------------------------------
REM Builds the portable version and prepares it for GitHub release.
REM This script:
REM   1. Runs the test suite to verify the code
REM   2. Builds the portable version using make_portable.bat
REM   3. Creates a versioned zip file for release
REM
REM Usage:
REM   build_release.bat [version]
REM
REM Example:
REM   build_release.bat v1.0.0
REM   build_release.bat (uses current date as version)
REM ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion

pushd "%~dp0.."
echo Working directory: %CD%
echo.

REM --- Parse version argument ------------------------------------------------
if "%~1"=="" (
    for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c%%a%%b)
    set VERSION=v%mydate%
    echo No version specified, using date-based version: %VERSION%
) else (
    set VERSION=%~1
    echo Using version: %VERSION%
)
echo.

REM --- Run tests ------------------------------------------------------------
echo === Running tests ===
python -m unittest discover -s tests -t .
if errorlevel 1 (
    echo.
    echo [ERROR] Tests failed. Fix these before building release.
    goto :finish_error
)
echo Tests passed.
echo.

REM --- Build portable version ------------------------------------------------
echo === Building portable version ===
cd packaging
call make_portable.bat
if errorlevel 1 (
    echo.
    echo [ERROR] Portable build failed.
    popd
    goto :finish_error
)
cd ..
echo.

REM --- Create versioned zip --------------------------------------------------
echo === Creating versioned release zip ===
set ZIP_NAME=dtt-wl-validator-portable-%VERSION%.zip
if exist "%ZIP_NAME%" del "%ZIP_NAME%"
rename dtt-wl-validator-portable.zip "%ZIP_NAME%"
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to rename zip file.
    goto :finish_error
)
echo Created: %ZIP_NAME%
echo.

REM --- Summary ---------------------------------------------------------------
echo ===========================================================
echo  Release build complete
echo ===========================================================
echo.
echo Files created:
echo   - portable\ (folder, for local testing)
echo   - %ZIP_NAME% (for GitHub release upload)
echo.
echo To upload to GitHub release:
echo   1. Create a new release with tag %VERSION%
echo   2. Upload %ZIP_NAME% as an asset
echo.
popd
echo Press any key to close.
pause >nul
exit /b 0

:finish_error
echo.
popd
echo Release build did not complete. Press any key to close.
pause >nul
exit /b 1

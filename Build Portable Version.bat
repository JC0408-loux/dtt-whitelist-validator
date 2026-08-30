@echo off
REM Build Portable Version for DTT Whitelist Validator
REM This script builds a portable version that can be used on any Windows machine
REM without requiring Python installation or internet connection.

echo ========================================
echo DTT Whitelist Validator - Build Portable Version
echo ========================================
echo.
echo This will:
echo 1. Download Python interpreter (if not cached)
echo 2. Copy application files
echo 3. Install required packages
echo 4. Create portable folder with all dependencies
echo 5. Generate ZIP file for distribution
echo.
echo The portable version will be created in the 'portable' folder.
echo Press any key to continue or Ctrl+C to cancel...
pause >nul

cd /d "%~dp0"
call packaging\make_portable.bat

if errorlevel 1 (
    echo.
    echo Build failed. Please check the error messages above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Build completed successfully!
echo ========================================
echo.
echo Portable version created in: portable\
echo ZIP file created: dtt-wl-validator-beta-v0.2-portable.zip
echo.
echo You can now:
echo 1. Copy the 'portable' folder to any Windows machine
echo 2. Or distribute the ZIP file
echo 3. No Python installation or internet connection required on target machine
echo.
pause
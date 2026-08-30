<#
    Builds a portable folder that runs the validator without installing
    anything, on this machine or on the test machines.

    Nothing here needs Python to already be present: the interpreter is
    downloaded as a plain archive and unpacked into the output folder. The
    result runs under python.exe, which is signed, so it is not treated the
    way an unsigned .exe is.

    Only this machine needs internet. Copy the finished folder to the test
    machines.
#>
[CmdletBinding()]
param(
    [string]$OutDir = "portable",
    # Force one source instead of trying them in order.
    [ValidateSet("", "standalone", "nuget", "embeddable")]
    [string]$Source = "",
    # For a machine that can reach none of the sources: fetch the archive by
    # hand and pass it here. -PythonArchiveSubdir names the folder inside it
    # that holds python.exe ("python" for standalone, "tools" for NuGet, ""
    # for the embeddable zip).
    [string]$PythonArchive = "",
    [string]$PythonArchiveSubdir = "python",
    [switch]$SkipExcel,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
$out = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir }
       else { Join-Path $root $OutDir }
$pythonDir = Join-Path $out "python"
$appDir = Join-Path $out "app"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("dttwl_" + [guid]::NewGuid().ToString("N"))

function Fail($text) {
    # PowerShell renders a `throw` as a one-line exception with a call stack,
    # which makes a multi-line explanation unreadable. Print it plainly.
    Write-Host ""
    Write-Host "BUILD FAILED" -ForegroundColor Red
    Write-Host ""
    foreach ($line in ($text -split "`r?`n")) { Write-Host $line -ForegroundColor Red }
    Write-Host ""
    exit 1
}

trap { Fail $_.Exception.Message }

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

function Invoke-Native {
    <#
        Runs an external program and returns its exit code and combined
        output. PowerShell otherwise turns anything the program writes to
        stderr into a NativeCommandError under $ErrorActionPreference = Stop,
        which buries the real message under a call-stack dump.
    #>
    param([string]$Exe, [string[]]$Arguments)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Exe @Arguments 2>&1 | ForEach-Object { $_.ToString() }
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output   = ($output -join [Environment]::NewLine)
        }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-File($url, $destination) {
    Note "downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing
}

function Expand-Any($archive, $destination) {
    if ($archive -match "\.tar\.gz$" -or $archive -match "\.tgz$") {
        # Windows 10 1803 and later ship bsdtar as tar.exe; Expand-Archive
        # cannot read a tar.gz.
        if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
            Fail "tar.exe is not available on this machine, so the .tar.gz cannot be unpacked. Use -Source nuget, or unpack it by hand and pass the folder."
        }
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        $result = Invoke-Native "tar" @("-xzf", $archive, "-C", $destination)
        if ($result.ExitCode -ne 0) { throw "tar failed: $($result.Output)" }
    } else {
        Expand-Archive -Path $archive -DestinationPath $destination -Force
    }
}

# ---------------------------------------------------------------------------
# 1. fetch an interpreter
# ---------------------------------------------------------------------------
# Only the standalone build carries tkinter, which the window needs. The NuGet
# package and the python.org embeddable package both ship without it; they are
# kept as fallbacks for command-line use, and the verification below says so
# rather than handing over a build that only fails on the test machine.

Step "Fetching Python"
New-Item -ItemType Directory -Force -Path $work | Out-Null

$standaloneBuilds = @(
    @{ Tag = "20241016"; Version = "3.11.10" },
    @{ Tag = "20240415"; Version = "3.11.9" }
)

$sources = @()
foreach ($build in $standaloneBuilds) {
    $sources += @{
        Key      = "standalone"
        Name     = "python-build-standalone $($build.Version) (full, includes tkinter)"
        Url      = "https://github.com/astral-sh/python-build-standalone/releases/download/$($build.Tag)/cpython-$($build.Version)%2B$($build.Tag)-x86_64-pc-windows-msvc-install_only.tar.gz"
        File     = "python-package.tar.gz"
        Sub      = "python"
        HasTk    = $true
    }
}
$sources += @{
    Key = "nuget"; Name = "NuGet python 3.11.9 (no tkinter)"
    Url = "https://www.nuget.org/api/v2/package/python/3.11.9"
    File = "python-package.zip"; Sub = "tools"; HasTk = $false
}
$sources += @{
    Key = "embeddable"; Name = "python.org embeddable 3.11.9 (no tkinter)"
    Url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    File = "python-package.zip"; Sub = ""; HasTk = $false
}

if ($Source) { $sources = $sources | Where-Object { $_.Key -eq $Source } }

$archive = $null
$chosen = $null

if ($PythonArchive) {
    if (-not (Test-Path $PythonArchive)) { throw "No such file: $PythonArchive" }
    $archive = Join-Path $work ([System.IO.Path]::GetFileName($PythonArchive))
    Copy-Item -Force $PythonArchive $archive
    $chosen = @{ Name = "local archive $PythonArchive"; Sub = $PythonArchiveSubdir }
    Note "using $($chosen.Name)"
} else {
    # Not $source: PowerShell variable names are case-insensitive, so that
    # would assign a hashtable to the $Source parameter and trip its
    # ValidateSet before a single download is attempted.
    foreach ($entry in $sources) {
        $candidate = Join-Path $work $entry.File
        try {
            Get-File $entry.Url $candidate
            $archive = $candidate
            $chosen = $entry
            Note "using $($entry.Name)"
            break
        } catch {
            Warn "could not fetch $($entry.Name): $($_.Exception.Message)"
        }
    }
}

if ($null -eq $chosen) {
    Fail @"
No Python package could be downloaded.

Check this machine's internet access or proxy. If everything is blocked,
fetch the archive on another machine and pass it in:

    make_portable.bat -PythonArchive C:\path\to\cpython-3.11.10.tar.gz

The build that includes tkinter (the window needs it) is:
    https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.11.10%2B20241016-x86_64-pc-windows-msvc-install_only.tar.gz
"@
}

$extracted = Join-Path $work "python-extracted"
Expand-Any $archive $extracted

$sourceDir = if ($chosen.Sub) { Join-Path $extracted $chosen.Sub } else { $extracted }
if (-not (Test-Path $sourceDir)) {
    Fail "The archive did not contain '$($chosen.Sub)'. Pass -PythonArchiveSubdir with the folder inside it that holds python.exe."
}

Step "Assembling $out"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item -Recurse -Force $sourceDir $pythonDir

$pythonExe = Join-Path $pythonDir "python.exe"
if (-not (Test-Path $pythonExe)) { throw "python.exe is missing from the archive." }
Note "interpreter: $pythonExe"

# An embeddable build isolates itself with a ._pth file; the application
# directory and site-packages have to be listed in it explicitly.
$pth = Get-ChildItem -Path $pythonDir -Filter "python*._pth" -ErrorAction SilentlyContinue |
       Select-Object -First 1
if ($pth) {
    Note "patching $($pth.Name) so the app and site-packages are importable"
    $lines = Get-Content $pth.FullName
    $lines += "..\app"
    $lines += "Lib\site-packages"
    Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII
}

# ---------------------------------------------------------------------------
# 2. copy the application
# ---------------------------------------------------------------------------
Step "Copying the application"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
Copy-Item -Recurse -Force (Join-Path $root "dttwl") $appDir
Copy-Item -Force (Join-Path $root "main.py") $appDir
Copy-Item -Force (Join-Path $root "config.example.json") $appDir
Copy-Item -Force (Join-Path $root "config.example.json") $out
Copy-Item -Force (Join-Path $root "README.md") $out
$probe = Join-Path $root "tools\dtt_probe.html"
if (Test-Path $probe) { Copy-Item -Force $probe $out }
Get-ChildItem -Recurse -Force -Path $appDir -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Note "app files copied"

# ---------------------------------------------------------------------------
# 3. openpyxl, for the .xlsx report
# ---------------------------------------------------------------------------
if ($SkipExcel) {
    Warn "skipping openpyxl; reports will be CSV only"
} else {
    Step "Adding openpyxl (for the .xlsx report)"
    $sitePackages = Join-Path $pythonDir "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
    try {
        foreach ($package in @("openpyxl", "et_xmlfile")) {
            $meta = Invoke-RestMethod -Uri "https://pypi.org/pypi/$package/json" -UseBasicParsing
            $wheel = $meta.urls | Where-Object { $_.packagetype -eq "bdist_wheel" } |
                     Select-Object -First 1
            if ($null -eq $wheel) { throw "no wheel published for $package" }
            $wheelZip = Join-Path $work "$package.zip"
            Get-File $wheel.url $wheelZip
            # A wheel is a zip; unpacking it into site-packages installs it.
            Expand-Archive -Path $wheelZip -DestinationPath $sitePackages -Force
            Note "$package $($meta.info.version) installed"
        }
    } catch {
        Warn "could not add openpyxl: $($_.Exception.Message)"
        Warn "the tool still works; reports will be CSV only"
    }
}

# ---------------------------------------------------------------------------
# 4. launchers
# ---------------------------------------------------------------------------
Step "Writing launchers"

$launcher = @'
@echo off
REM Opens the DTT Whitelist Validator. Nothing needs to be installed:
REM the Python runtime lives in the python\ folder beside this file.
setlocal
REM Work from this folder so config.json and reports\ land beside the tool
REM rather than in System32 when it is started as administrator.
cd /d "%~dp0"
"%~dp0python\python.exe" "%~dp0app\main.py" %*
if errorlevel 1 (
    echo.
    echo The tool exited with an error. Press any key to close.
    pause >nul
)
'@
Set-Content -Path (Join-Path $out "DTT Whitelist Validator.bat") -Value $launcher -Encoding ASCII

$console = @'
@echo off
REM Command line access, for example:
REM   command line.bat status
REM   command line.bat run --rounds 3
REM   command line.bat verify-stub
setlocal
cd /d "%~dp0"
"%~dp0python\python.exe" "%~dp0app\main.py" %*
echo.
pause
'@
Set-Content -Path (Join-Path $out "command line.bat") -Value $console -Encoding ASCII

$readme = @'
DTT Whitelist Validator - portable build
========================================

Nothing to install. Copy this whole folder to the test machine and run

    DTT Whitelist Validator.bat

The Python runtime is in python\ and the application is in app\. No internet
access is needed here.

For the command line (status, watch, run, verify-stub) use

    command line.bat status

README.md has the full documentation.
'@
Set-Content -Path (Join-Path $out "READ ME FIRST.txt") -Value $readme -Encoding ASCII
Note "launchers written"

# ---------------------------------------------------------------------------
# 5. verify before claiming success
# ---------------------------------------------------------------------------
Step "Verifying the build"

$version = Invoke-Native $pythonExe @("-c", "import sys; print(sys.version.split()[0])")
if ($version.ExitCode -ne 0) { throw "the bundled python.exe does not run:`n$($version.Output)" }
Note "python $($version.Output) runs"

$tk = Invoke-Native $pythonExe @("-c", "import tkinter")
if ($tk.ExitCode -ne 0) {
    Fail @"
The bundled Python has no tkinter, so the window cannot open.

Source used: $($chosen.Name)

The NuGet package and the python.org embeddable package both ship without
tkinter. Re-run using the standalone build, which includes it:

    make_portable.bat -Source standalone

If that download is blocked, fetch it on another machine and pass it in:

    https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.11.10%2B20241016-x86_64-pc-windows-msvc-install_only.tar.gz
    make_portable.bat -PythonArchive C:\path\to\that-file.tar.gz

The command line still works with this build ("command line.bat status"),
only the window does not.
"@
}
Note "tkinter present, the window can open"

$help = Invoke-Native $pythonExe @((Join-Path $appDir "main.py"), "--help")
if ($help.ExitCode -ne 0) { throw "the application does not start:`n$($help.Output)" }
Note "application starts"

$excel = Invoke-Native $pythonExe @("-c", "import openpyxl; print(openpyxl.__version__)")
if ($excel.ExitCode -eq 0) { Note "openpyxl $($excel.Output) present, .xlsx reports enabled" }
else { Warn "openpyxl missing, reports will be CSV only" }

# ---------------------------------------------------------------------------
# 6. pack it for copying
# ---------------------------------------------------------------------------
if (-not $NoZip) {
    Step "Packing"
    $zip = Join-Path $root "dtt-wl-validator-portable.zip"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Compress-Archive -Path $out -DestinationPath $zip
    Note "$zip"
}

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

Write-Host "`nDone." -ForegroundColor Green
Write-Host "Copy this folder to the test machine and run 'DTT Whitelist Validator.bat':"
Write-Host "  $out"

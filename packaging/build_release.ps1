<#
    Builds the portable version and prepares it for GitHub release.
    
    This script:
      1. Runs the test suite to verify the code
      2. Builds the portable version using make_portable.ps1
      3. Creates a versioned zip file for release
      4. Optionally runs a smoke test on the portable build
    
    Usage:
      .\build_release.ps1 [-Version <version>] [-SkipTests] [-SmokeTest]
    
    Examples:
      .\build_release.ps1 -Version v1.0.0
      .\build_release.ps1 (uses current date as version)
      .\build_release.ps1 -SkipTests (skip test suite)
      .\build_release.ps1 -SmokeTest (run smoke test after build)
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipTests,
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
$portableZip = Join-Path $root "dtt-wl-validator-portable.zip"

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" }
function Fail($text) {
    Write-Host ""
    Write-Host "BUILD FAILED" -ForegroundColor Red
    Write-Host ""
    foreach ($line in ($text -split "`r?`n")) { Write-Host $line -ForegroundColor Red }
    Write-Host ""
    exit 1
}

pushd $root
Step "Working directory: $root"

# ---------------------------------------------------------------------------
# 1. Determine version
# ---------------------------------------------------------------------------
if ([string]::IsNullOrEmpty($Version)) {
    $Version = "v" + (Get-Date).ToString("yyyyMMdd")
    Note "No version specified, using date-based version: $Version"
} else {
    Note "Using version: $Version"
}

# ---------------------------------------------------------------------------
# 2. Run tests
# ---------------------------------------------------------------------------
if (-not $SkipTests) {
    Step "Running test suite"
    $testResult = python -m unittest discover -s tests -t . 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "Tests failed:`n$testResult"
    }
    Note "All tests passed"
} else {
    Note "Skipping tests as requested"
}

# ---------------------------------------------------------------------------
# 3. Build portable version
# ---------------------------------------------------------------------------
Step "Building portable version"
$buildResult = & "$PSScriptRoot\make_portable.ps1" -NoZip:$false 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Portable build failed:`n$buildResult"
}
Note "Portable build completed"

# ---------------------------------------------------------------------------
# 4. Verify build output
# ---------------------------------------------------------------------------
Step "Verifying build output"
if (-not (Test-Path $portableZip)) {
    Fail "Portable zip not found: $portableZip"
}
$size = (Get-Item $portableZip).Length / 1MB
Note "Portable zip size: $([math]::Round($size, 2)) MB"

# ---------------------------------------------------------------------------
# 5. Create versioned zip
# ---------------------------------------------------------------------------
Step "Creating versioned release zip"
$versionedZip = Join-Path $root "dtt-wl-validator-portable-$Version.zip"
if (Test-Path $versionedZip) {
    Remove-Item -Force $versionedZip
}
Move-Item -Force $portableZip $versionedZip
Note "Created: $versionedZip"

# ---------------------------------------------------------------------------
# 6. Optional smoke test
# ---------------------------------------------------------------------------
if ($SmokeTest) {
    Step "Running smoke test on portable build"
    $portableDir = Join-Path $root "portable"
    $pythonExe = Join-Path $portableDir "python\python.exe"
    $mainPy = Join-Path $portableDir "app\main.py"
    
    if (-not (Test-Path $pythonExe)) {
        Fail "Python executable not found: $pythonExe"
    }
    
    Note "Testing: $pythonExe $mainPy --help"
    $helpResult = & $pythonExe $mainPy --help 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "Smoke test failed:`n$helpResult"
    }
    Note "Smoke test passed"
}

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
Step "Release build complete"
Write-Host ""
Write-Host "Files created:" -ForegroundColor Green
Write-Host "  - portable\ (folder, for local testing)"
Write-Host "  - $versionedZip (for GitHub release upload)"
Write-Host ""
Write-Host "To upload to GitHub release:" -ForegroundColor Yellow
Write-Host "  1. Create a new release with tag $Version"
Write-Host "  2. Upload $versionedZip as an asset"
Write-Host ""

popd
Write-Host "Done." -ForegroundColor Green

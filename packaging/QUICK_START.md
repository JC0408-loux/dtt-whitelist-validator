# Quick Start: Building Portable Version for Release

## One-Command Release (Recommended)

```bash
# Tag and push - GitHub Actions does the rest
git tag v1.0.0
git push origin v1.0.0
```

Download the zip from: https://github.com/YOUR_USERNAME/dtt-whitelist-validator/releases

## Local Build (for testing)

### Option A: Batch Script (Simple)
```cmd
cd packaging
build_release.bat v1.0.0
```

### Option B: PowerShell Script (More control)
```powershell
cd packaging
.\build_release.ps1 -Version v1.0.0 -SmokeTest
```

### Option C: Direct Build (No versioning)
```cmd
cd packaging
make_portable.bat
```

## What Gets Built

- `portable/` folder (~120 MB) - Ready to use
- `dtt-wl-validator-portable-v1.0.0.zip` - For GitHub release

## Verification

Tests run automatically before build. To verify manually:

```powershell
# Extract and test
Expand-Archive dtt-wl-validator-portable-v1.0.0.zip
cd portable
.\python\python.exe .\app\main.py --help
```

## Upload to GitHub

1. Go to https://github.com/YOUR_USERNAME/dtt-whitelist-validator/releases/new
2. Tag: `v1.0.0`
3. Upload: `dtt-wl-validator-portable-v1.0.0.zip`
4. Description: See RELEASE.md for template

## User Instructions

Tell users to:
1. Download the zip
2. Right-click → Properties → Unblock → OK
3. Extract
4. Run `DTT Whitelist Validator.bat`

## Troubleshooting

**Build fails with network error:**
- Download Python manually: https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.11.10%2B20241016-x86_64-pc-windows-msvc-install_only.tar.gz
- Run: `make_portable.bat -PythonArchive C:\path\to\file.tar.gz`

**Tests fail:**
- Fix tests before building (see AGENTS.md)

**Missing icons:**
- Ensure `icon/` folder exists in project root

See `packaging/RELEASE.md` for full documentation.

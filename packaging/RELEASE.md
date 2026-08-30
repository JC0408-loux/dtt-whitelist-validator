# Release Build Guide

This document describes how to build and release the portable version of DTT Whitelist Validator.

## Overview

The portable version is a self-contained folder that includes:
- Python 3.11.10 runtime (with tkinter)
- DTT Whitelist Validator application
- openpyxl for Excel report generation
- Launchers for GUI and command-line use

Users only need to download, unblock, extract, and run - no installation required.

## Build Methods

### Method 1: GitHub Actions (Recommended for Releases)

GitHub Actions automatically builds the portable version when you push a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow (`.github/workflows/release.yml`) will:
1. Run the test suite
2. Build the portable version
3. Create a GitHub Release
4. Upload the zip file as a release asset

You can also trigger the build manually from the GitHub Actions tab.

### Method 2: Local Build (Batch Script)

For local testing or manual release preparation:

```cmd
cd packaging
build_release.bat v1.0.0
```

This will:
1. Run the test suite
2. Build the portable version
3. Create `dtt-wl-validator-portable-v1.0.0.zip`

### Method 3: Local Build (PowerShell Script)

For more control over the build process:

```powershell
cd packaging
.\build_release.ps1 -Version v1.0.0
```

Available options:
- `-Version <version>`: Specify version (default: date-based)
- `-SkipTests`: Skip test suite
- `-SmokeTest`: Run smoke test on the portable build

Example with all options:
```powershell
.\build_release.ps1 -Version v1.0.0 -SmokeTest
```

### Method 4: Direct Portable Build

If you just need the portable folder without versioning:

```cmd
cd packaging
make_portable.bat
```

This creates:
- `portable/` folder (ready to use)
- `dtt-wl-validator-portable.zip` (in project root)

## Verification

### Automated Tests

The build scripts automatically run the test suite before building:

```bash
python -m unittest discover -s tests -t .
```

All 76 tests must pass before the build proceeds.

### Smoke Test

To verify the portable build works:

```powershell
cd packaging
.\build_release.ps1 -Version v1.0.0 -SmokeTest
```

This runs `portable/python/python.exe portable/app/main.py --help` to ensure the build is functional.

### Manual Verification

After building, manually test:

1. Extract the zip file
2. Run `DTT Whitelist Validator.bat`
3. Verify the window opens
4. Test connection to DTT
5. Run a simple test case

## What's Included in the Portable Build

The `portable/` folder contains:

```
portable/
├── python/                    # Python 3.11.10 runtime
│   ├── python.exe            # Signed interpreter
│   ├── Lib/                  # Standard library
│   └── Lib/site-packages/    # openpyxl, et_xmlfile
├── app/                       # Application code
│   ├── dttwl/                # Main package
│   ├── main.py               # Entry point
│   ├── config.example.json   # Example config
│   └── icon/                 # Application icons
├── DTT Whitelist Validator.bat  # GUI launcher
├── command line.bat          # CLI launcher
├── READ ME FIRST.txt         # Quick start guide
├── README.md                 # Full documentation
└── tools/dtt_probe.html      # DTT protocol probe tool
```

## Release Checklist

Before releasing:

- [ ] All tests pass (76 tests)
- [ ] Portable build completes successfully
- [ ] Smoke test passes
- [ ] Manual verification on a clean Windows machine
- [ ] Icon files are included in the build
- [ ] openpyxl is installed (for Excel reports)
- [ ] README.md is up to date
- [ ] Version number follows semantic versioning (vX.Y.Z)

## Troubleshooting

### Build Fails with "No Python package could be downloaded"

The build machine needs internet access to download Python. If blocked:

1. Download manually: https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.11.10%2B20241016-x86_64-pc-windows-msvc-install_only.tar.gz
2. Pass to the script:
   ```cmd
   make_portable.bat -PythonArchive C:\path\to\cpython-3.11.10.tar.gz
   ```

### Tests Fail

Fix the failing tests before building. See AGENTS.md for development guidelines.

### Portable Build Missing Icon Files

The build script automatically copies the `icon/` folder if it exists. Verify:
```cmd
dir icon
```

### Smart App Control Blocks the Files

Users must unblock the zip before extracting:
1. Right-click the zip file
2. Properties → Unblock → OK
3. Extract

Or use PowerShell:
```powershell
Get-ChildItem -Recurse "<folder>" | Unblock-File
```

## GitHub Actions Workflow Details

The workflow (`.github/workflows/release.yml`) runs on `windows-latest` and:

1. Checks out the code
2. Determines the version from the tag
3. Runs the test suite
4. Builds the portable version
5. Verifies the build output
6. Creates a GitHub Release
7. Uploads the zip file as a release asset
8. Uploads the portable folder as an artifact (for inspection)

The workflow is triggered by:
- Pushing a tag matching `v*.*.*` (e.g., v1.0.0)
- Manual dispatch from the Actions tab

## Versioning

Use semantic versioning: `vX.Y.Z`

- `X`: Major version (breaking changes)
- `Y`: Minor version (new features)
- `Z`: Patch version (bug fixes)

Example: `v1.2.3`

For date-based versions (e.g., for nightly builds): `v20240830`

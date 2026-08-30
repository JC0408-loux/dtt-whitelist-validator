# Cutting a release

## The automated path

Push a tag beginning with `v`:

```
git tag v0.3
git push origin v0.3
```

`.github/workflows/release.yml` then, on a Windows runner:

1. runs the whole test suite — a red suite stops the release here,
2. builds the portable folder with `packaging/make_portable.ps1`,
3. zips it under the name `dttwl/version.py` defines, and
4. creates (or updates) the GitHub release and attaches the zip.

`workflow_dispatch` runs the same job for a tag that already exists — use it
when a release was cut in the web UI and has no asset attached.

### Before tagging

- [ ] `dttwl/version.py` — bump `VERSION` and `VERSION_DISPLAY`. Everything
      else (window title, launcher `.bat` name, zip name) is derived from them.
- [ ] The tag matches `VERSION`. The workflow does not check this; a mismatch
      ships a zip whose name disagrees with its release.
- [ ] The commit being tagged is **on `main`**. A tag cut from `main` while the
      work sits on a feature branch produces a release containing none of it —
      this is what happened to `v0.2`, and nothing in git warns you about it.
      Confirm with `git branch --contains <commit>` before tagging.
- [ ] Anything touching `dttwl/winfg.py`, `dttwl/stub.py`, `dttwl/appicon.py`
      or `packaging/` has been run on a real Windows machine. None of it is
      covered by the test suite — see AGENTS.md §6.

## The manual path

If Actions is unavailable, on a Windows machine with internet access:

```cmd
packaging\make_portable.bat
```

It downloads a self-contained CPython, assembles `portable\`, verifies that
`import tkinter` works in it, and writes the zip to the repository root. Upload
that zip to the release by hand.

`Build Portable Version.bat` in the repository root does the same thing with a
friendlier prompt, for anyone who would rather not open a terminal.

## After releasing

Download the attached zip on a clean machine and check that:

- Right-click → Properties shows an **Unblock** box (it will — every download
  is marked; the release notes tell testers to clear it),
- the launcher `.bat` starts the window,
- the taskbar button shows the application icon rather than the Python icon,
- Settings shows a report folder under the signed-in user's Documents.

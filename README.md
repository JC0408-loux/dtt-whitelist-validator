# DTT Whitelist Mode Validation Tool

> Changing the code? Read **[AGENTS.md](AGENTS.md)** first: it carries the
> protocol details, the constraints that are not obvious from the code, and
> what can and cannot be verified without a test machine.

Checks that launching a whitelisted application actually makes Intel Dynamic
Tuning switch to the expected action set (`optimized_WL1` / `optimized_WL2`),
and reports every application that does not.

**Current version:** beta v0.2

## How it reads DTT

The DTT page at `http://localhost:8888/index.html` is only a shell. It opens a
WebSocket to `ws://localhost:8888/echo` and drives everything through a few
ESIF commands; the yellow highlighting is rendered client-side from the XML
those commands return.

This tool talks to that socket directly:

```
dptf ui getgroups                       -> Policies, Participants, ...
dptf ui getmodulesingroup 0             -> Adaptive Performance Policy, ...
dptf ui getmoduledata 0 0               -> the live status XML
```

The status XML carries everything needed, so no browser, no WebDriver and no
screen scraping are involved:

| Element | Meaning |
| --- | --- |
| `conditions_table` | one entry per action set, in arbitration order, with a true/false `result` for every minterm |
| `actions_table` | `action_id` -> action set name (`11` -> `optimized_WL1`) |
| `active_action` | the action set in effect: the first conditions row whose minterms are all true |
| `conditions_directory` | live values, including the `Workload` hint and `Power Source` |
| `request_directory` | what the active action set is actually applying (`PL1MAX`, `PL1MIN`, `IEOT`) |
| `workload_hint_configuration` | the executable-to-hint whitelist itself |

Not opening a browser matters: `msedge.exe` and `chrome.exe` are themselves on
the workload-hint 1 list, so a browser window taking focus mid-test would
change the very state being measured.

## Install

### Pre-built portable release (recommended)

The portable release is a self-contained package that includes everything needed
to run the tool — no Python installation, no internet connection, and no build
steps required on the test machine.

**Installation steps:**

1. **Download** the portable release ZIP file (e.g., `dtt-wl-validator-beta-v0.2-portable.zip`)
2. **Unblock** the ZIP file: right-click the .zip → Properties → tick *Unblock* → OK
3. **Extract** the ZIP file to any location
4. **Run** `DTT whitelist validator beta v0.2.bat` to launch the application

That's it. The portable folder is approximately 120 MB and fits comfortably on
a USB stick.

**What's included:**
- A self-contained Python interpreter with tkinter (for the GUI)
- All required dependencies (openpyxl for Excel reports)
- The complete application code
- Launch scripts for both GUI and command-line use

**Why this approach:**
- Nothing is installed or registered on the test machine
- The interpreter runs under the signed python.exe, so Smart App Control does not
  block it
- Works offline — no network access required during runtime
- The entire package is self-contained and can be copied or moved freely

### Building from source

If you need to build the portable release yourself (for development or custom
modifications), use:

```
packaging\make_portable.bat
```

This script downloads a self-contained CPython, unpacks it beside the application,
adds openpyxl, writes the launchers, and then verifies the result by actually
running it. The finished `portable\` folder (or the zip beside it) can be copied
to the test machine.

The interpreter comes from `python-build-standalone`, because it is the only
no-install Windows build that carries **tkinter** -- both the NuGet package and
the python.org embeddable package ship without it, and the window cannot open
without it. They remain as fallbacks for command-line use, and the verification
step fails with that explanation rather than handing over a build that only
breaks on the test machine.

If the download is blocked, fetch the archive on another machine and pass it in:

```
packaging\make_portable.bat -PythonArchive C:\path\to\cpython-3.11.10.tar.gz
```

`-Source standalone|nuget|embeddable` forces one source instead of trying them
in order.

### Single executable (alternative)

```
packaging\build.bat
```

Needs Python 3.8+ and internet on the build machine only -- PyInstaller has to
have a real interpreter to bundle, the same way a compiler does; the resulting
.exe carries Python inside it, which is why the test machines need neither.
Copy `dist\dtt-wl-validator.exe` plus a `config.json` across.

Tidier to hand over, but the .exe is unsigned, so Smart App Control blocks it
where it is switched on. Prefer the portable folder unless a single file
matters.

`run_from_source.bat` opens the same window from the source on any machine that
already has Python 3.8+.

### If Windows blocks the files

**"Smart App Control blocked a file that may be unsafe"** on a file extracted
from a downloaded .zip is the mark-of-the-web: every extracted file inherits it
and is then treated as untrusted. Clear it on the .zip *before* extracting --
right-click the .zip, Properties, tick *Unblock*, OK -- or clear it afterwards:

```
Get-ChildItem -Recurse "<folder>" | Unblock-File
```

If Smart App Control itself is switched on, it blocks unsigned executables
outright. Use the portable folder, which runs under the signed python.exe. The
alternative is turning Smart App Control off (Windows Security, App & browser
control), which cannot be undone without reinstalling Windows.

**A .bat closes immediately.** They all pause on every exit path now, so run it
again and read the message.

## The window

Starting the executable with no arguments opens the desktop window. It is a
native window, not a web page, for two reasons: a browser cannot launch local
executables at all, and msedge.exe and chrome.exe are themselves on the
workload-hint list, so a browser-based UI would change the very state being
measured. The window belongs to a process that is not whitelisted, which also
makes it the neutral baseline the runner returns to between test cases.

The application uses a custom icon (`icon/DTT_App_Icon.ico`) in the portable
release, replacing the default tkinter feather icon.

**Test** shows what is happening right now: the application being tested, the
action set DTT currently reports, and a banner that turns green on a pass and
red on a fail. It stays on top by default so it remains readable while the
application under test holds the foreground -- being on top is not the same as
having focus, so it does not disturb the measurement. Finished cases build up
in a list underneath, coloured the same way.

**Application Path** is where the applications come from. Point it at one
folder holding a shortcut (.lnk) or executable for each application under
test and press *scan*: the target is read out of each shortcut and matched
against the whitelist DTT reports, so "Adobe Photoshop 2024.lnk" lines up with
photoshop.exe. Anything in the folder that is not on the whitelist is ignored,
and anything on the whitelist with no path found is reported as `SKIP` rather
than as a failure. A path can also be typed in or picked per row.

**Settings** holds the DTT address, rounds, timings and launch mode, and a
*Test connection* button that reports each layer of the connection separately.

### When the DTT page opens but the tool cannot connect

Press *Test connection*, or run `dtt-wl-validator diagnose`. Rather than one
connect attempt, it walks the same path the tool takes and names the layer that
broke:

```
[ ok ] TCP port localhost:8888 reachable
[ ok ] HTTP server responds
       GET /index.html -> 200
[FAIL] WebSocket upgrade accepted
       handshake rejected: HTTP/1.1 403 Forbidden
[ -- ] ESIF commands answered
[ -- ] Policy module found
[ -- ] Policy status readable
```

The page rendering in a browser only proves the first two layers. Everything
after that is the WebSocket at `ws://localhost:8888/echo`, which is where the
page itself gets its data and where the tool reads the policy status.

Which headers that upgrade needs varies between DTT versions, so the tool tries
several handshakes in turn and keeps the first the server accepts. When none is
accepted the report lists every one it tried, against every address the host
resolves to, with what each attempt did:

```
[FAIL] WebSocket upgrade accepted
       localhost       standard              -> timed out waiting for data
       localhost       browser-like headers  -> 101 Switching Protocols
```

The distinction matters: DTT's web server closes the connection when it
rejects a handshake, and goes quiet only when it considers the request
incomplete. A timeout is therefore a different problem from a rejection.

## Use from the command line

```
dtt-wl-validator gui                     Open the window (the default).
dtt-wl-validator status                  Print the current DTT state and the
                                         whole conditions table, once.
dtt-wl-validator watch                   Print state changes as they happen.
dtt-wl-validator init-config --resolve   Generate config.json from DTT's own
                                         whitelist table and search for the
                                         executables.
dtt-wl-validator resolve-paths           Fill in blank exe_path values later.
dtt-wl-validator verify-stub             Check whether stub mode works here.
dtt-wl-validator run                     Run the sweep and write the report.
```

`run` exits 0 when everything passed, 1 when something failed, 2 on a setup
problem.

### Getting started

```
dtt-wl-validator status
dtt-wl-validator init-config --resolve
dtt-wl-validator run --rounds 3
```

`init-config` reads the application-to-hint mapping out of DTT itself, so the
expected mode for each executable cannot drift from what the platform actually
applies. Fill in `exe_path` for anything `--resolve` could not find; entries
with no path are reported as `SKIP`, never as a failure.

## Which action set each hint expects

Action sets are not named consistently across platforms - one machine calls
them `optimized_WL1` and `optimized_WL2`, another `AC_O_WL1` and `AC_O_WL2` -
so the tool does not match on the name. A row of the conditions table that
carries a `Workload == N` minterm **is** the action set for hint N, whatever it
is called, and that is read off the platform at every run.

Where a platform has separate rows for the same hint (an AC one and a DC one,
say), the row whose other conditions are currently satisfied is the one that
applies, so the mapping follows the machine's actual state rather than picking
whichever comes first.

The mapping is re-derived during preflight, not just when the whitelist is
loaded, so a config written on one machine adapts when it is copied to
another. Settings shows what was derived, and a name can be edited there to
override it - which is what `expected_mode_by_hint` stores.

## What a test case does

1. Focus this tool's own window and wait for DTT to return to the baseline
   action set. The validator is not whitelisted, so with its window in front
   the hint is absent and DTT falls back to `optimized`.
2. Launch the application and force its window to the foreground. The moment
   the foreground is confirmed is `t0`.
3. Poll DTT until the expected action set has been active for
   `stable_read_samples` consecutive reads. Polling from `t0`, rather than
   sleeping for the debounce and taking a single reading, both avoids catching
   a mid-switch state and measures the real switch latency.
4. Close the application, then measure how long DTT takes to fall back.

A pass needs `stable_read_samples` consecutive matching reads, so the earliest
one can be recorded is `debounce_buffer_seconds` plus the polling in between.
Preflight warns when that lands within three seconds of
`detect_timeout_seconds`, because a switch merely slower than usual would then
be reported as a failure.

A case that never reaches the expected mode is failed with the reason read
straight out of the conditions table, for example:

```
conditions not met: Workload == 2
conditions not met: Power Source == AC
conditions met but preempted by 'optimized_35' (row 1, higher priority)
```

`preflight` checks the blocking conditions once up front (AC power, the OEM
variables WL1/WL2 depend on) rather than letting thirty applications fail for
the same reason.

## Launching and closing

A shortcut is opened through the shell rather than by handing the path to
`explorer.exe`: explorer re-parses its own command line, so a shortcut whose
path contains a space made it open a folder window instead, which then owned
the foreground and made every reading wrong.

Only processes the test itself started are ever foregrounded or closed. The
processes already running when it launched are recorded first and left alone.

Browsers need more than that. Starting msedge.exe while Edge is already running
opens a window in the existing instance rather than a new process, so closing
it afterwards would close the user's own browser -- including the DTT page the
tool reads from. Browsers are therefore launched with a private
`--user-data-dir`, which forces a separate instance that can be closed on its
own. `browser_isolation` in the config controls which executables this applies
to.

## Launch modes

`run.mode = "real"` launches the installed applications.

`run.mode = "stub"` copies the interpreter under the target's name (for example
`photoshop.exe`), shows an empty window and brings that to the foreground. A
frozen build copies its own .exe to a temp folder; a portable build copies
python.exe within its own directory, where its DLLs and standard library are. If
DTT matches the whitelist on executable name alone, this validates every entry
in the table without installing any of the applications, and covers executables
that cannot be launched directly at all -- the 3DMark workload binaries are
started by the 3DMark GUI, not by hand.

That assumption is platform-specific. `verify-stub` proves it on the machine
first, and `run --mode stub` refuses to continue if the check fails.

## Report

Three files land in the report folder -- by default
`Documents\DTT Whitelist Validation Reports`, changeable on the Settings
tab. It is the signed-in user's Documents, not Public, so two engineers
sharing a test machine do not mix their runs:

`dtt_wl_report_<timestamp>.csv` is the summary, one line per application:

```
# | application    | APAT results  | pass/fail
1 | cinebench.exe  | optimized_WL2 | pass
2 | msedge.exe     | optimized_WL1 | pass
3 | steam.exe      | optimized     | fail
```

Rounds collapse into one line, and a single failing round makes the whole
application `fail` -- the action set shown is the one from that failing round,
so the table says what actually went wrong.

`dtt_wl_details_<timestamp>.csv` keeps every test case: expected and detected
action set, switch and de-assert latency, workload hint, power source,
temperature, the applied `PL1MAX`/`PL1MIN`, and the failure reason.

`dtt_wl_report_<timestamp>.xlsx` has both, as a green/red *Results* sheet and a
*Details* sheet, plus a *Summary* whose verdict distinguishes `FAIL` from
`INTERMITTENT`. Raise `run.rounds` above 1 to catch a mode switch that works
most of the time: a single sweep cannot tell intermittent from reliable.

## Configuration

| Key | Purpose |
| --- | --- |
| `dtt.host` / `dtt.port` | DTT web server, default `localhost:8888` |
| `timing.debounce_buffer_seconds` | expected APAT hint debounce; a switch slower than this passes but is flagged |
| `timing.detect_timeout_seconds` | how long to wait before failing a case |
| `timing.stable_read_samples` | consecutive matching reads required |
| `run.rounds` | rounds per application |
| `timing.poll_interval_seconds` | how often DTT is read while waiting for the switch |
| `run.mode` | `real` or `stub` |
| `expected_mode_by_hint` | override the derived action set for a hint; empty means derive |
| `baseline_mode` | idle action set; `null` learns it from the machine |
| `preflight.require_power_source` | usually `AC` |
| `search_paths` | directories `resolve-paths` searches |
| `shortcut_folder` | the folder of shortcuts the window scans |

See `config.example.json`.

## Development

```
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .
```

The tests run the whole pipeline against a simulated DTT server built from real
captures in `tests/fixtures/`, so the parser, arbitration logic, polling loop,
shortcut resolution, window and report writer are covered without an Intel
platform. The window tests skip themselves where tkinter has no display. Only
the Windows foreground control in `dttwl/winfg.py` needs the real thing.

## Building for release

Tagging a commit `v<something>` builds the portable zip on a Windows runner and
attaches it to the release automatically -- see `.github/workflows/release.yml`.
`packaging/RELEASE.md` has the checklist and the manual fallback.

To build the same zip locally, double-click `Build Portable Version.bat` in the
repository root, or:

```cmd
packaging\make_portable.bat
```

## Layout

```
dttwl/wsclient.py   minimal RFC 6455 client (standard library only)
dttwl/esif.py       ESIF command framing and module discovery
dttwl/status.py     status XML -> conditions, active action set, diagnosis
dttwl/winfg.py      launching, foreground control, closing (Windows)
dttwl/stub.py       renamed-executable stub window
dttwl/shortcuts.py  reading .lnk targets and matching them to the whitelist
dttwl/runner.py     preflight and the test loop
dttwl/gui.py        the desktop window
dttwl/report.py     CSV / XLSX output
dttwl/cli.py        command line
tools/dtt_probe.html  browser-based capture tool used to reverse the protocol
```

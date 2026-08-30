# AGENTS.md — read this before changing any code

This repository is **DTT Whitelist Validator**: a Windows tool that checks
whether launching a whitelisted application makes Intel Dynamic Tuning (DTT)
switch to the power/performance mode it is supposed to.

Two documents, two jobs:

- **README.md** — how to build, package and *use* the tool. Read it if you need
  to know what a command does.
- **AGENTS.md** (this file) — how to *change* the tool without breaking it.
  Read all of it before your first edit.

Most of what is written here was learned the hard way on real hardware. The
constraints in section 3 in particular are not style preferences: each one is a
bug that was hit, diagnosed and fixed.

---

## 1. What this tool validates

The platform behaves like this:

```
foreground application  ->  APAT sends a workload hint (1 or 2)
                        ->  [debounce]
                        ->  DTT's "Workload" condition changes
                        ->  a different action set becomes active
                        ->  different power limits (PL1MAX/PL1MIN) are applied
```

The hint follows the **foreground window**, not the existence of a process.
Starting an application without bringing it to the front proves nothing.

One test case therefore does:

1. Focus the tool's own window and wait for DTT to return to the baseline
   action set. The tool is not on the whitelist, so with its window in front no
   hint is asserted.
2. Launch the application and force its window to the foreground. The moment
   the foreground is *confirmed* is `t0`.
3. Poll DTT until the expected action set has been active for
   `stable_read_samples` consecutive reads. Latency is measured from `t0`.
4. Close the application and measure how long DTT takes to fall back.

**A case passes** when the expected action set is observed, stably, within
`detect_timeout_seconds`. **It fails** when it is not — and the failure reason
is read out of DTT's own conditions table, never guessed.

`SKIP` is not a failure: it means the application is not installed on this
machine. This is normal and common — a test machine will never have all 34.

---

## 2. How the tool talks to DTT

The page at `http://localhost:8888/index.html` is only a shell. It opens a
WebSocket and drives everything through a few ESIF commands. The tool speaks
that protocol directly — **it never automates a browser** (see section 3).

### Transport

```
ws://localhost:8888/echo
send:    <message id>:<command>
receive: <message id>:<payload>
```

Unsolicited `update:` and `status:` messages arrive on the same socket and must
be skipped.

### The only three commands used

| Command | Returns |
| --- | --- |
| `dptf ui getgroups` | Policies, Participants, Manager, Arbitrator, System |
| `dptf ui getmodulesingroup <group>` | the modules in that group |
| `dptf ui getmoduledata <group> <module>` | the module's live status XML |

The policy is found by name (`Adaptive Performance Policy`), never by a
hardcoded group/module number.

### The status XML

`dptf ui getmoduledata` returns everything the tool needs. No DOM scraping, no
colour matching.

| Element | Meaning |
| --- | --- |
| `conditions_table` | one entry per action set, **in arbitration order**, each with a true/false `result` per minterm |
| `actions_table` | `action_id` -> action set name (`11` -> `optimized_WL1`) |
| `active_action` | the `action_id` currently in effect |
| `conditions_directory` | live values: `Workload`, `Power Source`, temperatures, ... |
| `request_directory` | what the active action set is applying (`PL1MAX`, `PL1MIN`, `IEOT`) |
| `workload_hint_configuration` | the executable-to-hint whitelist itself |

### The arbitration rule

> **`active_action` is the first `conditions_table` entry, top to bottom, whose
> minterms are all true.**

This was verified against real captures and is reproduced in
`status.py::DttStatus.first_satisfied_row`. Nothing else decides which action
set wins.

### Action set names are derived, never matched

Action sets are named differently on different platforms:
`optimized_WL1` / `optimized_WL2` on one machine, `AC_O_WL1` / `AC_O_WL2` on
another. **Do not put either in the code.**

> A `conditions_table` row carrying a `Workload == N` minterm **is** the action
> set for hint N, whatever it is called.

Where a platform has separate rows for the same hint (an AC one and a DC one),
the row whose *other* minterms are currently true is the one that applies —
this is why derivation looks at live state, not just structure. See
`status.py::workload_action_set` and `derive_expected_modes`, and the
`status_ac_dc_named.xml` fixture which exists solely to cover this.

The mapping is re-derived during **preflight**, not only when the whitelist is
loaded, so a config written on one machine adapts when copied to another.

---

## 3. Hard constraints — doing any of these breaks the tool

Each line is a real defect that was diagnosed on hardware.

| | Rule | Why |
| --- | --- | --- |
| ❌ | Never open `.lnk` with `explorer.exe <path>` | explorer re-parses its own command line; a path containing a space is split and it opens a **Documents window** instead, which then owns the foreground and invalidates every reading. Use `os.startfile`. |
| ❌ | Never open more than a handful of WebSocket connections in a burst | DTT's web server has **`WS_MAX_CLIENTS = 10`** and does *not* time out a connection it considers incomplete. A burst exhausts it and the server starts refusing. One connection at a time, with a pause. |
| ❌ | Never fetch `index.html` as a health check | it is **4.1 MB**. Reading a few bytes and closing leaves the server holding the rest in a send buffer for a socket that is gone, which ties up a client slot and makes the next WebSocket attempts time out. Request a path that does not exist instead. |
| ❌ | Never introduce a dependency that needs network access at runtime | test machines are **offline**. Standard library only, plus `openpyxl` for the optional .xlsx report. |
| ❌ | Never automate a browser, and never let one take the foreground | `msedge.exe` and `chrome.exe` are themselves on the workload-hint list. A browser window stealing focus changes the very state being measured. This is also why the UI is a native window and not a web page. |
| ❌ | Never match an action set by name | see section 2. |
| ❌ | Never close a process that was already running | starting `msedge.exe` while Edge is open adds a window to the **existing** instance; closing it took down the tester's own browser, including the DTT page. Record which PIDs existed before launching and only ever touch new ones. |
| ✅ | Always launch browsers with a private `--user-data-dir` | it forces a separate instance that can be closed on its own. `browser_isolation` in the config controls which executables this applies to. |
| ✅ | Always keep preflight | it checks AC power and the OEM variables once, instead of letting thirty applications fail for the same reason. Other testers use this tool; do not add a path that skips it. |

### On failure reporting

A rejected WebSocket handshake and a silent one mean different things:
DTT's web server **closes the connection when it rejects** a handshake, and
**goes quiet only when it considers the request incomplete**. A timeout is
therefore not a rejection. Preserve that distinction in any error message.

---

## 4. Environment facts

These are properties of the platform, not bugs to fix.

- **`localhost:8888` is fixed.** Intel provides it on every machine that has
  DTT. The address does not vary between test machines.
- **DTT listens on IPv4 only.** `::1` is refused, `127.0.0.1` connects. This is
  true on all machines, not a local quirk. `diagnose.py` probes each resolved
  address separately for this reason.
- **Offline is the normal state.** The build machine has internet; the test
  machines do not.
- **Every download must be unblocked by hand** (right-click the .zip →
  Properties → Unblock, *before* extracting). Files extracted from a downloaded
  archive inherit the mark-of-the-web and Windows blocks them. This is an
  operating procedure, not a defect.
- **DTT versions differ between machines** because they follow Intel's driver
  releases. What that means for this code:

  | Survives a version change | Does not |
  | --- | --- |
  | action set names (derived) | the ESIF command names |
  | number of workload hints | the XML element names in section 2 |
  | AC/DC and other condition layouts | the `ws://.../echo` endpoint |

  If Intel changes the protocol, section 2 is what needs revisiting — and
  `tools/dtt_probe.html` is how to re-derive it from a live machine.

---

## 5. Architecture

```
main.py -> dttwl/cli.py -> gui.py (default)  or  a subcommand
                              |
        +---------------------+---------------------+
        |                     |                     |
    runner.py             winfg.py              report.py
   (the test loop)   (launch + foreground)   (CSV / XLSX)
        |
   detector -> esif.py -> wsclient.py -> ws://localhost:8888/echo
                  |
              status.py  (XML -> conditions, active action set, diagnosis)
```

| File | Lines | Role |
| --- | --- | --- |
| `wsclient.py` | 202 | minimal RFC 6455 client, standard library only |
| `esif.py` | 179 | ESIF command framing, module discovery, handshake variants |
| `status.py` | 277 | status XML -> conditions, arbitration, name derivation |
| `runner.py` | 490 | preflight and the test loop |
| `winfg.py` | 281 | **Windows only** — launching, foreground control, closing |
| `stub.py` | 143 | **Windows only** — the renamed-executable stub window |
| `shortcuts.py` | 147 | reads `.lnk` targets, matches them to the whitelist |
| `diagnose.py` | 300 | layered connection checks |
| `config.py` | 198 | defaults, validation, generation from the live tables |
| `report.py` | 242 | summary and detail reports |
| `gui.py` | 856 | the tkinter window |
| `cli.py` | 286 | command line |
| `appicon.py` | 121 | **Windows-specific behaviour** — taskbar identity and window icon |
| `paths.py` | 74 | where reports go by default (the user's Documents) |
| `version.py` | 17 | one source for the version, window title, and artifact names |

Supporting material:

- `tools/dtt_probe.html` — a browser page that talks to the same WebSocket.
  This is how the protocol in section 2 was reverse-engineered; use it again if
  a future DTT version changes something.
- `packaging/make_portable.bat` — builds a no-install folder for distribution.
  The pre-built portable release is the recommended way to ship the tool to
  test machines. Users simply download, unblock, extract, and run — no Python
  installation or build steps required.
- `docs/*.png` — screenshots of the window.
- `docs/v0.2-review.md` — a post-mortem of the v0.2 release: two features that
  shipped without working, a CI workflow that never fired, and a release tagged
  off the wrong commit. Every one of them passed a green test run. Read it
  before you trust a passing suite as evidence about a change you just made.

---

## 6. How to verify a change

```
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .
```

**107 tests.** They must all pass before you claim anything is done. Configure
this as the repository's test command so it runs automatically.

The tool itself has no third-party runtime dependency — test machines are
offline — but the tests do: `websockets`, because `tests/mock_dtt.py` serves a
real WebSocket endpoint rather than stubbing the handshake, and `openpyxl`,
because the report tests cover the .xlsx path. Without them two test modules
fail to import and the suite silently shrinks to 29 tests while still looking
like it ran. `.github/workflows/tests.yml` runs the full suite with tkinter and
Xvfb on every push, so the GUI tests execute instead of skipping themselves.

### The tests are the only ground truth available off-hardware

`tests/mock_dtt.py` simulates the DTT web server, driven by **real captures**
in `tests/fixtures/`:

| Fixture | State it represents |
| --- | --- |
| `status_wl1.xml` | Edge in the foreground, hint 1, `optimized_WL1` active |
| `status_wl2.xml` | Cinebench in the foreground, hint 2, `optimized_WL2` active |
| `status_ac_dc_named.xml` | a platform naming them `AC_O_WL*`, with separate AC and DC rows for hint 2 |

Any change to parsing, arbitration or name derivation must be checked against
all three. Do not edit a fixture to make a test pass — they are recordings of
real hardware.

### What cannot be verified without a Windows machine and live DTT

| Component | Why not |
| --- | --- |
| `winfg.py` | needs the real Win32 foreground APIs |
| `stub.py` | needs a real process and window |
| `appicon.py` | the taskbar button only exists on a real Windows desktop. The tests check *where the code looks* and that it never grabs the foreground; whether the icon actually appears is a hardware question |
| `paths.py` | the Windows branch resolves a redirected Documents folder (OneDrive, a network home drive). Off Windows only the fallback runs |
| `packaging/*` | produces and runs a Windows executable |
| a real DTT connection | the mock is a simulation, not the firmware |

**If your change touches any of these, say so plainly and mark it
`UNVERIFIED — needs a hardware test`.** Do not report it as complete, and do
not let a green test run imply it was covered. The maintainer will run it on a
machine and report back. Every change to these files in the project's history
was validated this way, and several were wrong on the first attempt.

### A useful self-check

Before claiming a fix works, answer: *which test would have caught the original
bug?* If the answer is "none", write that test.

---

## 7. Known issues

### 7.1 Baseline learning can latch onto a transient value — blocks stub mode

**Symptom.** Starting a test aborts with:

```
stub mode did not work on this machine: launching a renamed copy as
'chrome.exe' did not switch DTT to optimized_WL1 (machine was not at
baseline 'quiet' before the test case (active: optimized))
```

**Diagnosis.** The wording is misleading — the stub was never tested. The run
stopped at "wait for the machine to return to baseline".

`quiet`'s only condition is `OEM Variable 0 == 2`. That is a discrete firmware
value, not something that drifts. In every capture taken it is `0`. So at the
instant preflight learned the baseline, `OEM Variable 0` was momentarily `2` —
probably a vendor power-mode utility or a firmware transient — and the tool
recorded `quiet` as the baseline. Seconds later the value returned to `0`, the
machine settled on `optimized`, and the tool spent 20 seconds waiting for a
state that could no longer occur.

**Fix direction.** Baseline learning should not trust a single reading: sample
until stable, prefer a row with no volatile conditions, and re-learn rather
than abort when the recorded baseline stops being reachable.

**Impact.** This blocks the stub-mode verification below, so it is first in the
task list.

### 7.2 Stub mode is unverified — coverage is stuck at 4/34

The last full run: **4 pass, 30 skip**. The 30 are not installed, and never
will be — 3DMark's ten workload executables cannot even be launched directly.

Stub mode (`run.mode = "stub"`) launches a renamed copy of the tool showing an
empty window. **If DTT matches the whitelist on executable name alone**, this
validates every entry without installing anything, taking coverage from 12% to
100%. The mechanism is built and deliberately gated behind `verify-stub`, which
proves the assumption on the machine before any result is trusted.

That verification has not yet succeeded, because of 7.1.

### 7.3 The default timings leave almost no margin

```
earliest possible pass = debounce 5.0s + (3-1) reads x 2.0s = 9.0s
detect timeout                                              = 10.0s
margin                                                      =  1.0s
```

A switch merely slower than usual is reported as a failure. Preflight warns
when the margin is under three seconds (`runner.py::_warn_about_timing`). One
real measurement exists: **2.06 s** for Cinebench, so the 5 s debounce buffer
is conservative while the 10 s timeout is not. Task 4 addresses this properly
by deriving suggested timings from measured latencies.

---

## 8. Task list, in order

Each task is done when its acceptance check passes **and** its verification
status is stated honestly.

1. **Fix baseline learning (7.1).**
   Accept: baseline is only recorded once stable; an unreachable baseline
   triggers re-learning rather than an abort. Then `verify-stub` runs to a real
   verdict. *Needs a hardware test.*

2. **Treat foreground loss as INVALID, not FAIL.**
   The foreground is already known (`winfg.foreground_process_name`). During
   the measurement window, if the foreground is not the application under test,
   record `INVALID — foreground changed to <name>` and retry the case instead
   of failing it. Confirmed by experiment: clicking another window mid-test
   currently produces a false failure. This is the only remaining path that
   produces a *wrong conclusion*. *Needs a hardware test.*

3. **Make the report usable as a deliverable.**
   Run metadata (machine, DTT version, date, tester, power source, OEM
   variables, temperature, derived hint mapping); skips in their own section so
   they stop burying the results; write the report automatically at the end of
   a run rather than only on a button press.

4. **Suggest timings from measured data.**
   After a run, report the observed switch latencies and a recommended
   debounce buffer and detect timeout. Resolves 7.3 with evidence instead of
   guesswork.

5. **Window icon, glyphs, filtering.**
   Icon done: `icon/DTT_App_Icon.ico`, applied by `appicon.py` and bundled by
   both builds. *Still needs a hardware test — see the checklist in
   `packaging/RELEASE.md`.* Remaining: a ✓/✕ column so pass/fail does not
   depend on colour alone, and a filter for All / Failures only / Tested only.

6. **Convenience.**
   Re-run failures only; show live DTT state on the strip when idle; remember
   window size, position and the last shortcut folder.

---

## 9. Decision log

| Decision | Why |
| --- | --- |
| Speak the WebSocket protocol directly, not Selenium | no WebDriver to version-match, nothing to download on an offline machine, and no browser that could steal the foreground. The DTT page's own rendering is irrelevant — the XML has everything. |
| A native window, not a local web UI | a browser page cannot launch executables at all, and the browsers are on the whitelist. The window's process is not whitelisted, which also makes it the neutral baseline. |
| tkinter | in the standard library, so the portable build needs nothing extra. |
| `python-build-standalone` for the portable build | the only no-install Windows CPython that ships **tkinter**. The NuGet package and python.org's embeddable package both omit it, and a build without it fails only on the test machine — so `make_portable.bat` verifies `import tkinter` before declaring success. The pre-built portable release is distributed as a ZIP that users simply unblock, extract, and run — no Python installation or build steps required on test machines. |
| One-file PyInstaller build | stub mode copies the executable under another name, which only works if it is self-contained. |
| An AppUserModelID for the taskbar icon | Windows draws a taskbar button from the *process's* identity, not the window's. Without one, everything launched through `python.exe` — which the portable build always is — is grouped under Python and drawn with Python's icon. `iconbitmap` reaches only the title bar, and a `.lnk` carrying the icon decorates the shortcut, not the running process. |
| Reports default to the user's Documents, not Public | test machines are shared. A folder under `Public\Documents` mixes two engineers' runs together, and some sites deny writes to it by policy — which would surface as a permission error on the first export, long after the run that produced the data. |
| The Documents path is asked for, not assembled | on managed machines Documents is redirected to OneDrive or a network drive. `%USERPROFILE%\Documents` then names a folder Explorer no longer shows the tester. |
| The report file name carries no version | reports from several releases share one folder and are read as a set; a prefix that changes each release breaks sorting and any glob a tester writes. Version belongs in the report's metadata (task 3). |
| Stub mode gated behind `verify-stub` | it rests on DTT matching by filename. If that were wrong, every stub result would be a silent false failure. |
| Poll from `t0` instead of sleeping for the debounce | avoids reading mid-switch, and yields the real switch latency as a by-product. |
| Derive action set names from the platform | see section 2. |

---

## 10. Glossary

| Term | Meaning |
| --- | --- |
| **DTT** | Intel Dynamic Tuning Technology — adjusts power and thermal limits at runtime |
| **ESIF** | the command interface DTT exposes; the web UI is a client of it |
| **APAT** | the component that watches the foreground window and asserts the workload hint |
| **workload hint** | 1 or 2 here; which one is decided by the foreground executable's name |
| **action set** | a named set of actions DTT applies (`optimized_WL1`, `AC_O_WL2`, `optimized`, ...) |
| **minterm** | one condition in an action set's rule, e.g. `Workload == 2` |
| **APCT** | Adaptive Performance Conditions Table — the `conditions_table` in the XML |
| **baseline** | the action set that is active when no whitelisted application is in the foreground; usually `optimized` |
| **WL1 / WL2** | shorthand for the action sets selected by hint 1 and hint 2 |

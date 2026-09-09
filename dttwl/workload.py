# -*- coding: utf-8 -*-
"""Checking the Workload hint itself, rather than the action set it selects.

The whitelist runner in `runner.py` asks a question one step further down the
chain:

    foreground application -> APAT asserts a workload hint
                           -> DTT's "Workload" condition takes that value
                           -> an action set whose minterms include it wins
                           -> power limits change

`Runner` judges a case on the last-but-one step, the **action set**. This
runner judges it on the **hint**: the `Workload` row of DTT's conditions
directory -- the "Last Known Value" column the DTT page shows -- compared with
the hint that platform's own Workload Hint Configuration table assigns to that
executable.

Why it is worth having separately: an action set can fail to become active for
reasons that have nothing to do with the whitelist -- the machine is on
battery, an OEM variable is set, a higher-priority row won the arbitration. In
all of those the hint is still asserted correctly and the whitelist entry is
fine. Looking straight at the hint separates "APAT did not recognise this
executable" from "DTT chose not to act on it".

Everything else -- returning to baseline, launching, taking the foreground,
polling until the reading is stable, closing, measuring the de-assert -- is
inherited unchanged from `Runner`.
"""

from .report import PASS
from .runner import Runner, _join


class WorkloadRunner(Runner):
    """`Runner`, judging each case on DTT's Workload hint value."""

    OBSERVED = "workload hint"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The baseline here is the hint value with nothing whitelisted in the
        # foreground, not an action set name, so it comes from its own key and
        # is normally left unset for preflight to learn from the machine.
        baseline = self.config.get("baseline_workload")
        self.baseline = None if baseline is None else str(baseline)

    # -- what a case is judged on ------------------------------------------

    def _expected_for(self, app):
        hint = app.get("workload_hint")
        return "" if hint is None else str(hint)

    def _observe(self, status):
        return status.workload_value

    def _explain_miss(self, status, app):
        expected = self._expected_for(app)
        detected = status.workload_value
        if not detected or detected == "X":
            return (
                "DTT reported no workload hint while {0} held the foreground; "
                "APAT did not recognise it as a hint {1} application".format(
                    app["process_name"], expected)
            )
        return (
            "DTT reported workload hint {0} while {1} held the foreground, "
            "expected {2}".format(detected, app["process_name"], expected)
        )

    # -- preflight ---------------------------------------------------------

    def _check_expectations(self, status):
        """Every enabled application needs a hint to look for, and DTT has to
        be a platform that uses it.

        Deliberately *not* checked here: whether an action set exists for that
        hint. A platform can assert a hint that no action set consumes, and
        this run is still meaningful -- that is the whole point of looking at
        the hint rather than the action set.
        """
        known = set(status.workload_hints())
        if known:
            self.log("workload hints this platform uses: " + ", ".join(sorted(known)))

        problems = []
        for app in self._enabled_apps():
            expected = self._expected_for(app)
            if not expected:
                problems.append(
                    "{0} has no workload hint; reload the whitelist from DTT so "
                    "each application carries the hint DTT assigns it".format(
                        app["process_name"])
                )
                break
            if known and expected not in known:
                problems.append(
                    "{0} is configured for workload hint {1}, which this "
                    "platform does not use (it uses {2})".format(
                        app["process_name"], expected, ", ".join(sorted(known)))
                )
                break
        return problems

    # -- the loop ----------------------------------------------------------

    def _skip_reason(self, app, mode):
        if self._expected_for(app) == "":
            return "no workload hint on the DTT whitelist"
        return super()._skip_reason(app, mode)

    def _run_case(self, app, round_number, mode):
        row = super()._run_case(app, round_number, mode)
        expected = self._expected_for(app)
        # If the machine already sits at the hint under test with nothing
        # whitelisted in front, the case matches the instant it starts and
        # proves nothing. Say so rather than banking a green row.
        if row.result == PASS and expected and expected == self.baseline:
            row.notes = _join(
                row.notes,
                "inconclusive: the idle workload hint is already {0}, so this "
                "case cannot show that {1} asserted it".format(
                    expected, app["process_name"]),
            )
        return row


def scan(status):
    """[(process name, hint)] straight from DTT's Workload Hint Configuration.

    The listing the workload tab shows before anything is launched: which
    executables the platform whitelists, and which hint each one is meant to
    assert.
    """
    entries = []
    for hint, names in sorted(status.workload_groups.items()):
        for name in names:
            entries.append((name, str(hint)))
    return entries

"""Parsing of the Adaptive Performance Policy status XML served by DTT.

The XML is what `dptf ui getmoduledata <group> <module>` returns; the DTT web
page renders exactly this document through combined.xsl, so every piece of
state the yellow highlighting shows is available here directly:

  conditions_table   one entry per action set, in arbitration order, with a
                     true/false result for each minterm
  actions_table      action_id -> action_set name (e.g. 11 -> optimized_WL1)
  active_action      action_id of the action set currently in effect, which is
                     the first conditions_table entry whose minterms are all
                     true
  conditions_directory  live value of every condition, including Workload
  request_directory  the requests the active action set is actually applying
                     (PL1MAX, PL1MIN, IEOT, ...)
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

WORKLOAD_CONDITION = "Workload"


class StatusParseError(Exception):
    pass


@dataclass
class Minterm:
    condition: str
    comparison: str
    argument: str
    result: bool

    def describe(self):
        return "{0} {1} {2}".format(self.condition, self.comparison, self.argument)


@dataclass
class ConditionRow:
    order: int
    action_id: str
    action_set: str
    minterms: List[Minterm] = field(default_factory=list)

    @property
    def satisfied(self):
        return all(m.result for m in self.minterms)

    def failed_minterms(self):
        return [m for m in self.minterms if not m.result]

    def workload_argument(self):
        for m in self.minterms:
            if m.condition == WORKLOAD_CONDITION:
                return m.argument
        return None


@dataclass
class DttStatus:
    rows: List[ConditionRow]
    action_sets: Dict[str, str]
    active_action_id: Optional[str]
    conditions: Dict[str, str]
    requests: Dict[str, str]
    temperatures: Dict[str, str]
    oem_variables: List[str]
    workload_groups: Dict[str, List[str]]
    raw: str = ""

    # -- convenience -------------------------------------------------------

    @property
    def active_action_set(self):
        if self.active_action_id is None:
            return None
        return self.action_sets.get(self.active_action_id)

    @property
    def workload_value(self):
        return self.conditions.get(WORKLOAD_CONDITION)

    @property
    def power_source(self):
        return self.conditions.get("Power Source")

    def row_for(self, action_set):
        for row in self.rows:
            if row.action_set == action_set:
                return row
        return None

    def satisfied_rows(self):
        return [row for row in self.rows if row.satisfied]

    def first_satisfied_row(self):
        for row in self.rows:
            if row.satisfied:
                return row
        return None

    def explain(self, expected_action_set):
        """Say why `expected_action_set` is not the active one right now."""
        row = self.row_for(expected_action_set)
        if row is None:
            return "action set '{0}' does not exist in the conditions table".format(
                expected_action_set
            )

        failed = row.failed_minterms()
        if failed:
            return "conditions not met: " + "; ".join(m.describe() for m in failed)

        # Every minterm is true, so something above it in the table won it.
        active = self.first_satisfied_row()
        if active is not None and active.order < row.order:
            return (
                "conditions met but preempted by '{0}' (row {1}, higher priority)"
            ).format(active.action_set, active.order)
        return "conditions met but active action is '{0}'".format(self.active_action_set)

    def workload_action_set(self, hint, live=True):
        """The action set that workload hint `hint` selects on this platform.

        Read from the conditions table rather than matched by name: a row that
        carries a `Workload == N` minterm *is* the action set for hint N,
        whatever the OEM called it, so a platform naming them AC_O_WL1 and
        AC_O_WL2 needs no configuration of its own.

        With `live`, a row also has to have its other conditions currently
        satisfied, which picks the right one where a platform has separate AC
        and DC rows for the same hint. It falls back to the first matching row
        so that a machine on battery still reports a name rather than nothing.
        """
        hint = str(hint)
        structural = None

        for row in self.rows:
            workload_terms = [m for m in row.minterms
                              if m.condition == WORKLOAD_CONDITION]
            if not workload_terms:
                continue
            if not all(_compare(hint, m.comparison, m.argument)
                       for m in workload_terms):
                continue

            if structural is None:
                structural = row.action_set
            if not live:
                return structural
            if all(m.result for m in row.minterms
                   if m.condition != WORKLOAD_CONDITION):
                return row.action_set

        return structural

    def workload_hints(self):
        """Every hint value the platform mentions, from either table."""
        hints = set(self.workload_groups)
        for row in self.rows:
            for minterm in row.minterms:
                if (minterm.condition == WORKLOAD_CONDITION
                        and minterm.comparison == "=="):
                    hints.add(minterm.argument)
        return sorted(hints, key=lambda value: (not value.isdigit(), value))

    def predicted_action_set(self, workload_value):
        """Which action set should win if Workload were `workload_value`.

        Every non-Workload minterm keeps the result DTT just reported, so the
        prediction accounts for the live AC/DC state, temperature and OEM
        variables rather than assuming a clean bench.
        """
        target = str(workload_value)
        for row in self.rows:
            ok = True
            for m in row.minterms:
                if m.condition == WORKLOAD_CONDITION:
                    if not _compare(target, m.comparison, m.argument):
                        ok = False
                        break
                elif not m.result:
                    ok = False
                    break
            if ok:
                return row.action_set
        return None


def _compare(value, comparison, argument):
    if comparison == "==":
        return value == argument
    if comparison == "!=":
        return value != argument
    try:
        left, right = float(value), float(argument)
    except (TypeError, ValueError):
        return False
    if comparison == "<":
        return left < right
    if comparison == "<=":
        return left <= right
    if comparison == ">":
        return left > right
    if comparison == ">=":
        return left >= right
    return False


def _text(node, tag, default=""):
    found = node.find(tag)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def parse_status(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise StatusParseError("could not parse status XML: {0}".format(exc))

    if root.tag != "adaptive_performance_policy_status":
        raise StatusParseError("unexpected root element <{0}>".format(root.tag))

    action_sets = {}
    for entry in root.findall("./actions_table/actions_table_entry"):
        action_id = _text(entry, "action_id")
        name = _text(entry, "action_set")
        if action_id and name:
            action_sets.setdefault(action_id, name)

    rows = []
    for order, entry in enumerate(root.findall("./conditions_table/conditions_table_entry")):
        action_id = _text(entry, "action_id")
        row = ConditionRow(
            order=order,
            action_id=action_id,
            action_set=action_sets.get(action_id, "action_id " + action_id),
        )
        for op in entry.findall("logical_operation"):
            for minterm in op.findall("minterm"):
                row.minterms.append(
                    Minterm(
                        condition=_text(minterm, "condition"),
                        comparison=_text(minterm, "comparison"),
                        argument=_text(minterm, "argument"),
                        result=_text(minterm, "result").lower() == "true",
                    )
                )
        rows.append(row)

    conditions = {}
    for cond in root.findall("./conditions_directory/condition"):
        if _text(cond, "is_in_use").lower() != "true":
            continue
        conditions[_text(cond, "condition_type")] = _text(cond, "current_value")

    requests = {}
    for req in root.findall("./request_directory/request"):
        code = _text(req, "code")
        argument = _text(req, "argument")
        if code:
            requests.setdefault(code, argument)

    temperatures = {}
    for target in root.findall("./thresholds/target"):
        name = _text(target, "target_name")
        value = _text(target, "current_temperature")
        if name and value:
            temperatures[name] = value

    oem_variables = [
        (v.text or "").strip() for v in root.findall("./oem_variables/variable")
    ]

    workload_groups = {}
    for group in root.findall("./workload_hint_configuration/workload_group"):
        group_id = _text(group, "id")
        names = []
        for app in group.findall("./applications/application"):
            names.extend(split_application_names(app.text or ""))
        if group_id:
            workload_groups[group_id] = names

    active = root.find("active_action")
    active_id = None
    if active is not None and active.text:
        active_id = active.text.strip()

    return DttStatus(
        rows=rows,
        action_sets=action_sets,
        active_action_id=active_id,
        conditions=conditions,
        requests=requests,
        temperatures=temperatures,
        oem_variables=oem_variables,
        workload_groups=workload_groups,
        raw=xml_text,
    )


def split_application_names(text):
    """Split one <application> cell into individual executable names.

    DTT stores several executables in a single cell ("olk.exe; outlook.exe",
    and a ten-item list for 3DMark), separated by semicolons and padded with
    stray whitespace and line breaks.
    """
    names = []
    for chunk in text.replace("\n", ";").replace("\r", ";").split(";"):
        name = " ".join(chunk.split())
        if name:
            names.append(name.lower())
    return names


def derive_expected_modes(status, overrides=None):
    """hint value -> expected action set, from the platform, minus overrides."""
    overrides = {str(key): value
                 for key, value in (overrides or {}).items() if value}
    mapping = {}
    for hint in status.workload_hints():
        if hint in overrides:
            mapping[hint] = overrides[hint]
            continue
        name = status.workload_action_set(hint)
        if name:
            mapping[hint] = name
    return mapping

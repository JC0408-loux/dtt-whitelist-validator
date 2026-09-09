"""Version information for DTT Whitelist Validator.

This module provides a single source of truth for version information
across the application, including window titles, file names, and reports.
"""

VERSION = "v0.2"
VERSION_DISPLAY = "beta v0.2"
FULL_TITLE = f"DTT whitelist validator {VERSION_DISPLAY}"
BAT_FILE_NAME = f"DTT whitelist validator {VERSION_DISPLAY}.bat"
ZIP_FILE_NAME = f"dtt-wl-validator-{VERSION_DISPLAY.replace(' ', '-')}-portable.zip"
# The prefix names the test the report came from, so the two kinds do not have
# to be told apart by opening them. `_report_` is rewritten to `_details_` for
# the per-round file, so keep that word in both.
REPORT_PREFIX = f"dtt_action_set_report_{VERSION_DISPLAY.replace(' ', '_')}"
WORKLOAD_REPORT_PREFIX = f"dtt_workload_hint_report_{VERSION_DISPLAY.replace(' ', '_')}"

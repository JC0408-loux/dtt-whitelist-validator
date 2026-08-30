"""Version information for DTT Whitelist Validator.

This module provides a single source of truth for version information
across the application, including window titles, file names, and reports.
"""

VERSION = "v0.2"
VERSION_DISPLAY = "beta v0.2"
FULL_TITLE = f"DTT whitelist validator {VERSION_DISPLAY}"
BAT_FILE_NAME = f"DTT whitelist validator {VERSION_DISPLAY}.bat"
ZIP_FILE_NAME = f"dtt-wl-validator-{VERSION_DISPLAY.replace(' ', '-')}-portable.zip"

# Deliberately *not* versioned.  Reports from different tool versions land in
# one folder and are read as a set: a prefix that changes each release breaks
# sorting and any glob a tester writes.  The version belongs in the report's
# own metadata, not in its file name.
REPORT_PREFIX = "dtt_wl_report"

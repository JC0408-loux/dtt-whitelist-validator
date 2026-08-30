"""Tests for the report location, the icon lookup, and what ships in a build.

None of this can prove the icon actually appears on a Windows taskbar - that
needs a real machine, and AGENTS.md section 6 says so.  What is checked here is
everything that *can* be: that the code looks in the right places, that the
default report folder is the signed-in user's rather than a shared one, that
nothing calls the API which would steal the foreground, and that the build
scripts and the code agree on file names.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dttwl import appicon, paths, version
from dttwl import config as config_module
from dttwl.report import timestamped_paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ReportLocationTests(unittest.TestCase):
    def test_the_default_folder_is_under_the_users_documents(self):
        folder = paths.default_report_dir()
        self.assertTrue(os.path.isabs(folder), folder)
        self.assertTrue(folder.endswith(paths.REPORT_FOLDER_NAME), folder)
        self.assertIn("documents", folder.lower())

    def test_the_default_folder_is_not_shared_between_users(self):
        # Public\Documents is writable by everyone and readable by everyone,
        # so two engineers on one test machine would pile runs into one folder
        # - and some sites deny writes to it outright.
        folder = paths.default_report_dir().lower()
        self.assertNotIn("public", folder)
        self.assertNotIn("all users", folder)

    def test_no_drive_letter_is_hard_coded(self):
        # A literal C:\ breaks on a machine whose profile lives elsewhere.
        source = _read("dttwl", "paths.py") + _read("dttwl", "config.py")
        self.assertNotIn("C:\\\\Users", source)

    def test_an_empty_output_dir_in_a_config_file_means_the_default(self):
        # config.example.json ships "" so it names nobody's user profile; it
        # must not be read as "write into the working directory".
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"report": {"output_dir": "   "}}, handle)
            loaded = config_module.load(path)
        self.assertEqual(loaded["report"]["output_dir"], paths.default_report_dir())

    def test_the_shipped_example_config_leaves_the_folder_to_the_default(self):
        import json
        with open(os.path.join(ROOT, "config.example.json"), encoding="utf-8") as handle:
            example = json.load(handle)
        self.assertEqual(example["report"]["output_dir"], "")

    def test_the_config_default_matches_the_function(self):
        self.assertEqual(config_module.DEFAULTS["report"]["output_dir"],
                         paths.default_report_dir())

    def test_the_literal_path_is_not_duplicated_across_modules(self):
        # It used to be pasted into four places; changing one would silently
        # desync the others.
        for name in ("gui.py", "config.py"):
            source = _read("dttwl", name)
            self.assertNotIn("DTT whitelist validation report", source, name)

    def test_documents_dir_falls_back_when_windows_cannot_answer(self):
        # SHGetKnownFolderPath is unavailable off Windows and can fail on it;
        # either way a path has to come back rather than an exception.
        original = paths._known_folder
        paths._known_folder = lambda folder_id: None
        try:
            self.assertEqual(paths.documents_dir(),
                             os.path.join(os.path.expanduser("~"), "Documents"))
        finally:
            paths._known_folder = original


class ReportNameTests(unittest.TestCase):
    def test_the_prefix_carries_no_version_and_no_dot(self):
        # A dot before the timestamp reads as an extension to Excel and to any
        # glob a tester writes, and a versioned prefix breaks sorting across
        # releases when reports share a folder.
        self.assertNotIn(".", version.REPORT_PREFIX)
        self.assertNotIn(version.VERSION, version.REPORT_PREFIX)

    def test_report_names_sort_by_time(self):
        written = timestamped_paths("out", ["csv", "xlsx"])
        name = os.path.basename(written["csv"])
        self.assertRegex(name, r"^dtt_wl_report_\d{8}_\d{6}\.csv$")


class IconLookupTests(unittest.TestCase):
    def test_the_icon_is_actually_in_the_repository(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "icon", appicon.ICON_NAME)))

    def test_the_icon_is_found_from_the_package(self):
        found = appicon.icon_file()
        self.assertIsNotNone(found, "icon/DTT_App_Icon.ico was not located")
        self.assertTrue(os.path.isabs(found))

    def test_a_frozen_build_looks_inside_the_bundle_first(self):
        # PyInstaller unpacks datas into _MEIPASS. Looking next to the .exe
        # instead - which is what sys.path[0] gives - finds nothing.
        sys._MEIPASS = os.path.join(ROOT, "does-not-exist")
        try:
            self.assertEqual(appicon._candidate_dirs()[0], sys._MEIPASS)
        finally:
            del sys._MEIPASS

    def test_the_lookup_survives_a_missing_icon(self):
        original = appicon.ICON_NAME
        appicon.ICON_NAME = "no_such_icon.ico"
        try:
            self.assertIsNone(appicon.icon_file())
        finally:
            appicon.ICON_NAME = original

    def test_setting_the_app_id_is_a_no_op_off_windows(self):
        # It must report failure rather than raise, so the window still opens.
        if os.name != "nt":
            self.assertFalse(appicon.set_app_user_model_id())

    def test_applying_a_missing_icon_fails_without_touching_the_window(self):
        # No icon must mean "no icon", not a crash on startup. `None` stands in
        # for the window: reaching it at all would raise.
        original = appicon.ICON_NAME
        appicon.ICON_NAME = "no_such_icon.ico"
        try:
            self.assertFalse(appicon.apply_window_icon(None))
        finally:
            appicon.ICON_NAME = original


class ForegroundSafetyTests(unittest.TestCase):
    def test_the_icon_code_never_grabs_the_foreground(self):
        # The whole tool measures which window is in front. A startup path that
        # calls SetForegroundWindow corrupts the thing being measured; it was
        # in the v0.2 icon code, labelled "force window redraw".
        self.assertNotIn("SetForegroundWindow", _read("dttwl", "appicon.py"))
        self.assertNotIn("SetForegroundWindow", _read("dttwl", "gui.py"))

    def test_foreground_control_stays_in_the_modules_that_own_it(self):
        # winfg.py drives the foreground for the test; stub.py raises its own
        # window, which is the entire point of a stub. Nothing else may.
        allowed = {"winfg.py", "stub.py"}
        offenders = []
        package = os.path.join(ROOT, "dttwl")
        for name in sorted(os.listdir(package)):
            if not name.endswith(".py") or name in allowed:
                continue
            if "SetForegroundWindow" in _read("dttwl", name):
                offenders.append(name)
        self.assertEqual(offenders, [])


def _workflow_directives():
    """release.yml with comment lines dropped.

    The comments name the things the workflow deliberately avoids, so matching
    against the raw file would find every string these tests forbid.
    """
    lines = _read(".github", "workflows", "release.yml").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


class PackagedArtifactTests(unittest.TestCase):
    """The build scripts and the code have to agree on names and contents."""

    def test_the_pyinstaller_spec_bundles_the_icon(self):
        spec = _read("packaging", "dtt_wl_validator.spec")
        self.assertIn("DTT_App_Icon.ico", spec)
        self.assertIn("datas=[(ICON", spec)
        self.assertIn("icon=ICON", spec)

    def test_the_portable_build_reads_the_version_instead_of_repeating_it(self):
        # It used to carry its own copy under a comment saying "should match
        # dttwl/version.py". A comment is not a mechanism; the build would have
        # produced a zip labelled with the previous release.
        script = _read("packaging", "make_portable.ps1")
        self.assertIn("dttwl\\version.py", script)
        self.assertNotIn('$VERSION = "v', script)
        self.assertNotIn('$VERSION_DISPLAY = "', script)

    def test_the_readme_names_the_current_artifacts(self):
        # A tester follows these names literally; a stale one sends them
        # looking for a file the release does not contain.
        readme = _read("README.md")
        self.assertIn(version.VERSION_DISPLAY, readme)
        self.assertIn(version.ZIP_FILE_NAME, readme)
        self.assertIn(version.BAT_FILE_NAME, readme)

    def test_the_portable_build_ships_the_icon(self):
        self.assertIn("DTT_App_Icon.ico", _read("packaging", "make_portable.ps1"))

    def test_the_release_workflow_matches_two_part_tags(self):
        # 'v*.*.*' never matched v0.2, so v0.2 shipped with no attached zip.
        workflow = _workflow_directives()
        self.assertIn("- 'v*'", workflow)
        self.assertNotIn("v*.*.*", workflow)

    def test_the_release_workflow_asks_python_for_the_zip_name(self):
        # Hard-coding it is how the workflow came to look for a file the build
        # had stopped producing.
        workflow = _workflow_directives()
        self.assertIn("ZIP_FILE_NAME", workflow)
        self.assertNotIn("dtt-wl-validator-portable.zip", workflow)

    def test_the_release_workflow_tests_before_it_builds(self):
        workflow = _workflow_directives()
        self.assertLess(workflow.index("Run tests"),
                        workflow.index("Build portable version"))

    def test_the_release_workflow_uses_maintained_actions(self):
        # actions/create-release and upload-release-asset were archived in
        # 2021, and create-release fails outright on an existing release.
        workflow = _workflow_directives()
        self.assertNotIn("actions/create-release", workflow)
        self.assertNotIn("actions/upload-release-asset", workflow)

    def test_both_workflows_install_the_test_dependencies(self):
        # Without them tests/mock_dtt.py fails to import, two whole modules
        # disappear, and the run still reports a tidy pass on what is left.
        required = _read("requirements-dev.txt")
        self.assertIn("websockets", required)
        self.assertIn("openpyxl", required)
        for workflow in ("release.yml", "tests.yml"):
            content = _read(".github", "workflows", workflow)
            self.assertIn("requirements-dev.txt", content, workflow)

    def test_every_third_party_test_import_is_declared(self):
        # A new import in tests/ that nobody lists here passes locally and
        # fails on a clean runner, which is how tests.yml first went red.
        declared = _read("requirements-dev.txt").lower()
        stdlib = set(sys.stdlib_module_names)
        local = {"dttwl", "tests"}
        tests_dir = os.path.join(ROOT, "tests")
        for name in sorted(os.listdir(tests_dir)):
            if not name.endswith(".py"):
                continue
            for line in _read("tests", name).splitlines():
                line = line.strip()
                if line.startswith("import "):
                    module = line[len("import "):].split()[0].split(".")[0]
                elif line.startswith("from "):
                    module = line[len("from "):].split()[0].split(".")[0]
                else:
                    continue
                if module in stdlib or module in local or not module:
                    continue
                self.assertIn(module.lower(), declared,
                              "{0} imports {1}, which requirements-dev.txt "
                              "does not list".format(name, module))

    def test_ci_never_invokes_a_script_that_pauses(self):
        # make_portable.bat ends in `pause`; a runner has nobody to press a key.
        workflow = _workflow_directives()
        self.assertNotIn("make_portable.bat", workflow)

    def test_there_is_one_documented_way_to_build_each_artifact(self):
        # v0.2 accumulated six entry points doing three jobs. Each artifact
        # gets exactly one build script, plus the two root-level launchers a
        # non-programmer is meant to double-click.
        packaging = {name for name in os.listdir(os.path.join(ROOT, "packaging"))
                     if name.endswith((".bat", ".ps1"))}
        self.assertEqual(
            packaging,
            {"build.bat",           # single-file .exe
             "make_portable.bat",   # portable folder, wrapper
             "make_portable.ps1"})  # portable folder, the real build
        root = {name for name in os.listdir(ROOT) if name.endswith(".bat")}
        self.assertEqual(
            root,
            {"Build Portable Version.bat",  # friendly build entry point
             "run_from_source.bat"})        # Smart App Control fallback

    def test_the_headless_gui_tool_is_present_and_documented(self):
        # It is the only way to look at the window off Windows, and it is worth
        # nothing if nobody knows it exists - which is exactly why the v0.2
        # report path shipped with a green suite.
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "tools", "run_gui_headless.py")))
        for parts in (("AGENTS.md",), ("README.md",)):
            self.assertIn("tools/run_gui_headless.py", _read(*parts), parts)

    def test_the_docs_do_not_point_at_deleted_scripts(self):
        for parts in (("README.md",), ("AGENTS.md",), ("packaging", "RELEASE.md")):
            self.assertNotIn("build_release", _read(*parts), parts)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


if __name__ == "__main__":
    unittest.main()

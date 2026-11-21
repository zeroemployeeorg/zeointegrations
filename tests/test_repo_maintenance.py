"""Synthetic guard and diagnostic checks; no provider calls or shared hook writes."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import repo_maintenance as maintenance


class MainPushGuardTests(unittest.TestCase):
    def run_hook(self, refs):
        return subprocess.run(
            ["sh", str(maintenance.HOOK)],
            input=refs,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        ).returncode

    def test_main_update_and_deletion_are_refused(self):
        for sha in ("1" * 40, "0" * 40):
            with self.subTest(sha=sha):
                self.assertEqual(
                    self.run_hook(
                        f"refs/heads/topic {sha} refs/heads/main {'2' * 40}\n"
                    ),
                    1,
                )

    def test_any_main_in_multiple_ref_updates_is_refused(self):
        self.assertEqual(
            self.run_hook("topic 1 refs/heads/topic 2\ntopic 1 refs/heads/main 2\n"),
            1,
        )

    def test_other_branches_and_tags_are_allowed(self):
        for ref in ("refs/heads/topic", "refs/heads/main-other", "refs/tags/main"):
            with self.subTest(ref=ref):
                self.assertEqual(self.run_hook(f"topic 1 {ref} 2\n"), 0)

    def test_empty_input_is_allowed(self):
        self.assertEqual(self.run_hook(""), 0)


class MaintenanceTests(unittest.TestCase):
    def test_hook_installation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "hooks" / "pre-push"
            with (
                patch.object(maintenance, "hook_path", return_value=target),
                patch.object(maintenance, "command", return_value=None),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(maintenance.hooks(), 0)
                before = target.stat().st_mtime_ns
                self.assertEqual(maintenance.hooks(), 0)
                self.assertEqual(target.stat().st_mtime_ns, before)
                self.assertEqual(target.read_bytes(), maintenance.HOOK.read_bytes())
                self.assertTrue(os.access(target, os.X_OK))

    def test_existing_hook_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pre-push"
            target.write_text("existing owner hook")
            with (
                patch.object(maintenance, "hook_path", return_value=target),
                patch.object(maintenance, "command", return_value=None),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(maintenance.hooks(), 1)
                self.assertEqual(target.read_text(), "existing owner hook")

    def test_custom_hook_directory_is_not_taken_over(self):
        with (
            patch.object(maintenance, "command", return_value="custom"),
            patch.object(maintenance, "hook_path") as path,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(maintenance.hooks(), 1)
            path.assert_not_called()

    def test_doctor_does_not_install_or_print_environment_values(self):
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"FLOOR_PYTHON": "/private/secret-sentinel", "DOCTOR_NETWORK": "0"},
            ),
            patch.object(maintenance, "command", return_value=None),
            patch.object(maintenance, "matching_hook", return_value=False),
            patch.object(maintenance, "hooks") as install,
            patch.object(maintenance.urllib.request, "urlopen") as network,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(maintenance.doctor(), 1)
            install.assert_not_called()
            network.assert_not_called()
        self.assertNotIn("secret-sentinel", output.getvalue())
        self.assertIn("FIX", output.getvalue())

    def test_clean_defaults_to_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            with (
                patch.object(maintenance, "ROOT", root),
                patch.dict(os.environ, {"CLEAN_CONFIRM": "0"}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(maintenance.clean(), 0)
                self.assertTrue((root / "dist").exists())

    def test_confirmed_clean_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep").mkdir()
            (root / "dist").symlink_to(root / "keep", target_is_directory=True)
            with (
                patch.object(maintenance, "ROOT", root),
                patch.dict(os.environ, {"CLEAN_CONFIRM": "1"}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(maintenance.clean(), 1)
                self.assertTrue((root / "keep").is_dir())


if __name__ == "__main__":
    unittest.main()

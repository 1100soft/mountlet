from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_stage_rclone():
    root = Path(__file__).resolve().parents[1]
    path = root / "packaging" / "stage_rclone.py"
    spec = importlib.util.spec_from_file_location("mountlet_stage_rclone_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load stage_rclone.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageRcloneTests(unittest.TestCase):
    def test_windows_tiny_executable_is_rejected_as_shim(self):
        stage_rclone = _load_stage_rclone()
        with tempfile.TemporaryDirectory() as tempdir:
            fake = Path(tempdir) / "rclone.exe"
            fake.write_bytes(b"shim")
            with mock.patch.object(stage_rclone, "_windows_real_rclone_candidates", return_value=[]):
                with mock.patch.object(stage_rclone.shutil, "which", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "package-manager shims cannot be bundled"):
                        stage_rclone.resolve_rclone_source(fake, "Windows")

    def test_resolver_accepts_executable_that_reports_rclone_version(self):
        stage_rclone = _load_stage_rclone()
        with tempfile.TemporaryDirectory() as tempdir:
            fake = Path(tempdir) / "rclone"
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.object(
                stage_rclone.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="rclone v1.70.0\n", stderr=""),
            ) as run:
                self.assertEqual(stage_rclone.resolve_rclone_source(fake, "Linux"), fake.resolve())

        run.assert_called_once_with(
            [str(fake.resolve()), "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_resolver_rejects_non_rclone_output(self):
        stage_rclone = _load_stage_rclone()
        with tempfile.TemporaryDirectory() as tempdir:
            fake = Path(tempdir) / "rclone"
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.object(
                stage_rclone.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="not rclone\n", stderr=""),
            ):
                with self.assertRaisesRegex(RuntimeError, "usable rclone"):
                    stage_rclone.resolve_rclone_source(fake, "Linux")

    def test_resolver_rejects_timed_out_binary(self):
        stage_rclone = _load_stage_rclone()
        with tempfile.TemporaryDirectory() as tempdir:
            fake = Path(tempdir) / "rclone"
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.object(
                stage_rclone.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(str(fake), 15),
            ):
                with self.assertRaisesRegex(RuntimeError, "usable rclone"):
                    stage_rclone.resolve_rclone_source(fake, "Linux")


if __name__ == "__main__":
    unittest.main()

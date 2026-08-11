from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import tempfile
import unittest
import zipfile
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


def _load_build_linux_bundle():
    root = Path(__file__).resolve().parents[1]
    path = root / "packaging" / "build_linux_bundle.py"
    spec = importlib.util.spec_from_file_location("mountlet_build_linux_bundle_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load build_linux_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageRcloneTests(unittest.TestCase):
    def test_windows_installer_preserves_running_rclone_mounts(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "windows" / "mountlet.iss").read_text(
            encoding="utf-8"
        )
        spec = (root / "packaging" / "mountlet.spec").read_text(encoding="utf-8")

        self.assertIn("CloseApplicationsFilterExcludes=rclone.exe", installer)
        self.assertIn('f"vendor/rclone/{version}"', spec)

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

    def test_windows_resolver_finds_chocolatey_portable_binary(self):
        stage_rclone = _load_stage_rclone()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            shim = root / "chocolatey" / "bin" / "rclone.exe"
            real = root / "chocolatey" / "lib" / "rclone.portable" / "tools" / "rclone-v1.74.3-windows-amd64" / "rclone.exe"
            shim.parent.mkdir(parents=True)
            real.parent.mkdir(parents=True)
            shim.write_bytes(b"shim")
            real.write_bytes(b"x" * (stage_rclone.WINDOWS_SHIM_MAX_BYTES + 1))
            with mock.patch.object(stage_rclone.shutil, "which", return_value=str(shim)):
                with mock.patch.dict(stage_rclone.os.environ, {"ChocolateyInstall": str(root / "chocolatey")}):
                    with mock.patch.object(
                        stage_rclone.subprocess,
                        "run",
                        return_value=SimpleNamespace(returncode=0, stdout="rclone v1.74.3\n", stderr=""),
                    ):
                        self.assertEqual(stage_rclone.resolve_rclone_source(None, "Windows"), real.resolve())

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

    def test_bundled_rclone_uses_official_download_archive(self):
        stage_rclone = _load_stage_rclone()
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            def fake_download(_url: str, archive: Path) -> None:
                with zipfile.ZipFile(archive, "w") as handle:
                    handle.writestr("rclone-v1.74.3-osx-amd64/rclone", b"official")

            with mock.patch.object(stage_rclone.urllib.request, "urlretrieve", side_effect=fake_download) as download:
                binary = stage_rclone.download_official_rclone(root, "Darwin", arch="amd64")

            self.assertEqual(binary.read_bytes(), b"official")
            self.assertTrue(binary.stat().st_mode & 0o755)
            self.assertIn("rclone-current-osx-amd64.zip", download.call_args.args[0])


class BuildLinuxBundleTests(unittest.TestCase):
    def test_launcher_uses_system_python_and_app_local_library_path(self):
        build_linux_bundle = _load_build_linux_bundle()
        with tempfile.TemporaryDirectory() as tempdir:
            launcher = Path(tempdir) / "Mountlet"

            build_linux_bundle.write_launcher(launcher)

            text = launcher.read_text(encoding="utf-8")
            self.assertIn('PYTHON_BIN="${MOUNTLET_PYTHON:-python3}"', text)
            self.assertIn('PYTHONPATH="$APP_DIR/lib', text)
            self.assertIn('-m mountlet.desktop "$@"', text)
            if os.name != "nt":
                self.assertTrue(launcher.stat().st_mode & 0o111)

    def test_preview_build_info_defaults_to_preview_report_api(self):
        build_linux_bundle = _load_build_linux_bundle()
        with mock.patch.dict(
            build_linux_bundle.os.environ,
            {
                "GITHUB_REF_NAME": "wip",
                "GITHUB_RUN_NUMBER": "314",
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_SHA": "1234567890abcdef",
            },
            clear=True,
        ):
            data = build_linux_bundle.build_info_data()

        self.assertEqual(data["channel"], "preview")
        self.assertEqual(data["buildId"], "r314.2-12345678")
        self.assertEqual(data["licenseApiUrl"], "https://wip.mountlet.pages.dev/api/license")
        self.assertEqual(data["licenseSiteUrl"], "https://wip.mountlet.pages.dev")
        self.assertEqual(data["noticeApiUrl"], "https://wip.mountlet.pages.dev/api/notices")
        self.assertEqual(data["reportApiUrl"], "https://wip.mountlet.pages.dev/api/report")

    def test_production_build_info_keeps_production_services_separate(self):
        build_linux_bundle = _load_build_linux_bundle()
        with mock.patch.dict(
            build_linux_bundle.os.environ,
            {
                "GITHUB_REF_NAME": "v0.6.3",
                "GITHUB_RUN_NUMBER": "315",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_SHA": "abcdef1234567890",
            },
            clear=True,
        ):
            data = build_linux_bundle.build_info_data()

        self.assertEqual(data["channel"], "production")
        self.assertEqual(data["buildId"], "r315.1-abcdef12")
        self.assertEqual(data["licenseApiUrl"], "https://mountlet.app/api/license")
        self.assertEqual(data["licenseSiteUrl"], "https://mountlet.app")
        self.assertEqual(data["noticeApiUrl"], "https://mountlet.app/api/notices")
        self.assertEqual(data["reportApiUrl"], "https://mountlet.app/api/report")

    def test_pull_request_artifacts_are_preview_builds(self):
        build_linux_bundle = _load_build_linux_bundle()
        with mock.patch.dict(
            build_linux_bundle.os.environ,
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REF_NAME": "42/merge",
                "GITHUB_HEAD_REF": "feature",
            },
            clear=True,
        ):
            data = build_linux_bundle.build_info_data()

        self.assertEqual(data["channel"], "preview")
        self.assertEqual(data["licenseSiteUrl"], "https://wip.mountlet.pages.dev")

    def test_install_build_info_writes_to_installed_package(self):
        build_linux_bundle = _load_build_linux_bundle()
        with tempfile.TemporaryDirectory() as tempdir:
            package = Path(tempdir) / "mountlet"
            package.mkdir()

            build_linux_bundle.install_build_info(Path(tempdir))

            self.assertTrue((package / "mountlet-build-info.json").is_file())


class WebsiteReleaseTests(unittest.TestCase):
    def test_public_beta_entry_and_key_remain_connected(self):
        root = Path(__file__).resolve().parents[2]
        index = (root / "web" / "index.html").read_text(encoding="utf-8")
        config = (root / "web" / "config.js").read_text(encoding="utf-8")
        public_vars = (root / "functions" / "_lib" / "public-vars.js").read_text(encoding="utf-8")
        script = (root / "web" / "script.js").read_text(encoding="utf-8")

        self.assertIn('id="use-beta-key"', index)
        pattern = r'publicBetaKey: "(MTB-[A-Z2-9]{5}(?:-[A-Z2-9]{5}){3})"'
        config_key = re.search(pattern, config)
        server_key = re.search(pattern, public_vars)
        self.assertIsNotNone(config_key)
        self.assertIsNotNone(server_key)
        self.assertEqual(config_key.group(1), server_key.group(1))
        self.assertIn("window.MOUNTLET_SITE_CONFIG?.license?.publicBetaKey", script)


if __name__ == "__main__":
    unittest.main()

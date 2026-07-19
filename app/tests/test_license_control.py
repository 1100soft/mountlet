from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from mountlet import license_control
from mountlet.platform_services.linux import LinuxPlatformServices


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class LicenseControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        env = {
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
            license_control.TRIAL_DURABLE_DIR_ENV: str(root / "durable"),
        }
        env_patcher = mock.patch.dict("os.environ", env, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        platform = LinuxPlatformServices()
        platform_patcher = mock.patch("mountlet.config_tools.shared.get_platform", return_value=platform)
        platform_patcher.start()
        self.addCleanup(platform_patcher.stop)

    def test_trial_state_is_replicated_and_not_plain_json(self):
        now = time.time()
        record = license_control.load_or_create_trial(now=now)

        self.assertIn("install_id", record)
        for path in (
            Path(self.tempdir.name) / "state" / "mountlet" / "license" / "trial.dat",
            Path(self.tempdir.name) / "config" / "mountlet" / ".license-trial",
            Path(self.tempdir.name) / "cache" / "mountlet" / ".license-trial",
            Path(self.tempdir.name) / "durable" / ".mountlet-trial",
            Path(self.tempdir.name) / "durable" / ".mountlet-trial-backup",
        ):
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("install_id", text)

    def test_trial_survives_app_state_removal_when_durable_marker_exists(self):
        start = 1_700_000_000.0
        license_control.load_or_create_trial(now=start)
        for path in (
            Path(self.tempdir.name) / "state" / "mountlet" / "license" / "trial.dat",
            Path(self.tempdir.name) / "config" / "mountlet" / ".license-trial",
            Path(self.tempdir.name) / "cache" / "mountlet" / ".license-trial",
        ):
            path.unlink()

        status = license_control.current_status(now=start + 3 * 24 * 60 * 60)

        self.assertEqual(status.state, "trial")
        self.assertLessEqual(status.trial_days_remaining, 4)

    def test_machine_hint_uses_stable_linux_machine_id(self):
        with (
            mock.patch("mountlet.license_control.platform.system", return_value="Linux"),
            mock.patch("mountlet.license_control.platform.machine", return_value="x86_64"),
            mock.patch("mountlet.license_control.Path.read_text", return_value="stable-machine-id\n"),
            mock.patch("mountlet.license_control.uuid.getnode", side_effect=AssertionError("not stable")),
        ):
            first = license_control.machine_hint()
            second = license_control.machine_hint()

        self.assertEqual(first, second)

    def test_macos_machine_identifier_handles_mocked_process_without_stdout(self):
        with (
            mock.patch("mountlet.license_control.platform.system", return_value="Darwin"),
            mock.patch("mountlet.license_control.platform.node", return_value="test-mac"),
            mock.patch("mountlet.license_control.socket.gethostname", return_value="test-mac"),
            mock.patch("mountlet.license_control.subprocess.run", return_value=SimpleNamespace(returncode=0)),
            mock.patch("mountlet.license_control.uuid.getnode", return_value=(1 << 40) | 123),
        ):
            identifier = license_control._stable_machine_identifier()

        self.assertEqual(identifier, "test-mac|test-mac")

    def test_replicated_legacy_trial_migrates_to_stable_machine_id(self):
        start = 1_700_000_000.0
        legacy_hint = "a" * 64
        record = {
            "version": 1,
            "install_id": "legacy-install",
            "machine_hint": legacy_hint,
            "started_at": start,
            "last_seen_at": start,
        }
        payload = license_control._b64encode(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        envelope = {
            "payload": payload,
            "signature": license_control._trial_signature(payload, machine=legacy_hint),
        }
        encoded = license_control._b64encode(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        paths = license_control._trial_paths()
        for path in paths[:2]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded + "\n", encoding="utf-8")

        with mock.patch("mountlet.license_control.machine_hint", return_value="b" * 64):
            migrated = license_control.load_or_create_trial(now=start + 86_400)

        self.assertEqual(migrated["started_at"], start)
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["machine_hint"], "b" * 64)

    def test_trial_uses_earliest_valid_start(self):
        start = 1_700_000_000.0
        license_control.load_or_create_trial(now=start)
        status = license_control.current_status(now=start + 2 * 24 * 60 * 60)

        self.assertEqual(status.state, "trial")
        self.assertLessEqual(status.trial_days_remaining, 5)
        self.assertIn("ends ", status.summary)
        self.assertRegex(status.expires_at, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_expired_trial_is_not_allowed(self):
        start = 1_700_000_000.0
        license_control.load_or_create_trial(now=start)
        status = license_control.current_status(now=start + 8 * 24 * 60 * 60)

        self.assertEqual(status.state, "expired")
        self.assertFalse(status.allowed)
        self.assertIn("Trial expired ", status.summary)

    def test_expire_trial_for_debug_ends_trial_immediately(self):
        now = 1_700_000_000.0
        license_control.load_or_create_trial(now=now)

        license_control.expire_trial_for_debug(now=now)
        status = license_control.current_status(now=now)

        self.assertEqual(status.state, "expired")
        self.assertFalse(status.allowed)

    def test_license_token_signature_is_verified(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        token = self._signed_token(
            private_key,
            {
                "licenseId": "lic_1",
                "deviceId": "dev_1",
                "email": "user@example.com",
                "plan": "Personal",
                "maxDevices": 4,
                "deviceLabel": "Laptop",
            },
        )

        with mock.patch.dict("os.environ", {license_control.LICENSE_PUBLIC_KEY_ENV: public_pem}, clear=False):
            payload = license_control.verify_license_token(token)
            license_control.store_license_token(token)
            status = license_control.current_status()

        self.assertEqual(payload["email"], "user@example.com")
        self.assertEqual(status.state, "licensed")
        self.assertEqual(status.max_devices, 4)

    def test_beta_license_kind_is_displayed(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        token = self._signed_token(
            private_key,
            {
                "licenseId": "lic_1",
                "deviceId": "dev_1",
                "email": "tester@example.com",
                "plan": "Beta",
                "licenseKind": "beta",
                "maxDevices": 2,
            },
        )

        with mock.patch.dict("os.environ", {license_control.LICENSE_PUBLIC_KEY_ENV: public_pem}, clear=False):
            license_control.store_license_token(token)
            status = license_control.current_status()

        self.assertEqual(status.license_kind, "beta")
        self.assertIn("Beta license", status.summary)

    def test_expired_license_resets_trial(self):
        start = 1_700_000_000.0
        license_control.load_or_create_trial(now=start - 20 * 24 * 60 * 60)
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        token = self._signed_token(
            private_key,
            {
                "licenseId": "lic_1",
                "deviceId": "dev_1",
                "plan": "Monthly",
                "licenseKind": "paid",
                "maxDevices": 1,
                "expiresAt": "2000-01-01T00:00:00Z",
            },
        )

        with mock.patch.dict("os.environ", {license_control.LICENSE_PUBLIC_KEY_ENV: public_pem}, clear=False):
            license_control.store_license_token(token)
            license_control.store_license_key("MNT-AAAAA-BBBBB-CCCCC-DDDDD")
            status = license_control.current_status(now=start)

        self.assertEqual(status.state, "trial")
        self.assertGreaterEqual(status.trial_days_remaining, 6)
        self.assertEqual(license_control.load_license_key(), "")

    def test_unverifiable_license_does_not_show_trial(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        token = self._signed_token(
            private_key,
            {
                "licenseId": "lic_1",
                "deviceId": "dev_1",
                "plan": "Monthly",
                "licenseKind": "paid",
                "maxDevices": 1,
            },
        )

        license_control.store_license_token(token)
        status = license_control.current_status()

        self.assertEqual(status.state, "expired")
        self.assertIn("License cannot be verified", status.summary)
        self.assertEqual(status.expires_at, "")

    def test_license_key_is_stored_and_cleared(self):
        license_control.store_license_key("MNT-AAAAA-BBBBB-CCCCC-DDDDD")

        self.assertEqual(license_control.load_license_key(), "MNT-AAAAA-BBBBB-CCCCC-DDDDD")
        self.assertTrue((Path(self.tempdir.name) / "state" / "mountlet" / "license" / "license-key.txt").exists())

        license_control.clear_license_key()

        self.assertEqual(license_control.load_license_key(), "")

    def test_packaged_license_urls_use_build_info(self):
        encodings: list[str] = []

        class FakeResource:
            def is_file(self) -> bool:
                return True

            def read_text(self, *, encoding: str) -> str:
                encodings.append(encoding)
                return (
                    '{"licenseApiUrl":"https://wip.mountlet.pages.dev/api/license",'
                    '"licenseSiteUrl":"https://wip.mountlet.pages.dev"}'
                )

        fake_files = mock.Mock(return_value=SimpleNamespace(joinpath=mock.Mock(return_value=FakeResource())))
        with mock.patch("mountlet.license_control.files", fake_files):
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(license_control.license_site_url(), "https://wip.mountlet.pages.dev")
                self.assertEqual(
                    license_control._api_endpoint(None, "activate"),
                    "https://wip.mountlet.pages.dev/api/license/activate",
                )

        self.assertEqual(encodings, ["utf-8", "utf-8"])

    def test_invalid_license_token_is_rejected(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        other_key = ec.generate_private_key(ec.SECP256R1())
        public_pem = other_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        token = self._signed_token(private_key, {"licenseId": "lic_1"})

        with mock.patch.dict("os.environ", {license_control.LICENSE_PUBLIC_KEY_ENV: public_pem}, clear=False):
            with self.assertRaises(RuntimeError):
                license_control.verify_license_token(token)

    def test_raw_es256_license_token_signature_is_verified(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        token = self._signed_token(private_key, {"licenseId": "lic_1"}, raw_signature=True)

        with mock.patch.dict("os.environ", {license_control.LICENSE_PUBLIC_KEY_ENV: public_pem}, clear=False):
            payload = license_control.verify_license_token(token)

        self.assertEqual(payload["licenseId"], "lic_1")

    def _signed_token(
        self,
        private_key: ec.EllipticCurvePrivateKey,
        payload: dict[str, object],
        *,
        raw_signature: bool = False,
    ) -> str:
        header = {"alg": "ES256" if raw_signature else "ES256-DER", "typ": "Mountlet-License"}
        encoded_header = _b64(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        encoded_payload = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
        signature = private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
        if raw_signature:
            r, s = decode_dss_signature(signature)
            signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{encoded_header}.{encoded_payload}.{_b64(signature)}"


if __name__ == "__main__":
    unittest.main()

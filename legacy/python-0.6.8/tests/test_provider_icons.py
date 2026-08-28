from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.provider_icons import PROVIDER_ASSETS, _provider_key


class ProviderIconTests(unittest.TestCase):
    def test_new_provider_assets_are_registered(self) -> None:
        self.assertEqual(PROVIDER_ASSETS["gphotos"], "google-photos.svg")
        self.assertEqual(PROVIDER_ASSETS["iclouddrive"], "icloud.svg")
        self.assertEqual(PROVIDER_ASSETS["protondrive"], "proton-drive.svg")
        self.assertEqual(PROVIDER_ASSETS["mega"], "mega.svg")
        self.assertEqual(PROVIDER_ASSETS["nextcloud"], "nextcloud.svg")

    def test_generic_s3_uses_aws_asset(self) -> None:
        self.assertEqual(PROVIDER_ASSETS["s3"], "amazon-s3.svg")
        self.assertEqual(PROVIDER_ASSETS["other"], "amazon-s3.svg")
        self.assertEqual(_provider_key("s3", provider_name="", extra_info={}), "s3")
        self.assertEqual(
            _provider_key("s3", provider_name="Other", extra_info={"provider": "Other"}),
            "other",
        )


if __name__ == "__main__":
    unittest.main()

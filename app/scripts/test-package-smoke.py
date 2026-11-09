import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('smoke', Path(__file__).with_name('smoke-installed-macos.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeTests(unittest.TestCase):
    def test_resource_directory_is_not_a_bundled_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / 'vendor/rclone/version'
            directory.mkdir(parents=True)
            self.assertFalse(smoke.contains_bundled_rclone(root))
            (directory / 'rclone').write_text('fixture executable')
            self.assertTrue(smoke.contains_bundled_rclone(root))

    @unittest.skipUnless(hasattr(os, 'killpg'), 'POSIX process groups used by the macOS smoke runner')
    def test_readiness_exit_and_failure_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'marker.json'
            valid = dict(version='fixture', **{key: True for key in ['buildId','frontendReady','mainWindowReady','mainWindowVisible','remoteStateReady','behaviorComplete']})
            for mode in ['success','never-ready','ready-hang','invalid','failed-exit']:
                with self.subTest(mode=mode):
                    marker.unlink(missing_ok=True)
                    source = f'import pathlib,time,sys; p=pathlib.Path({str(marker)!r}); '
                    if mode != 'never-ready':
                        source += f'p.write_text({json.dumps(valid if mode != "invalid" else {})!r}); '
                    source += 'time.sleep(30)' if mode in ['never-ready','ready-hang'] else f'sys.exit({7 if mode == "failed-exit" else 0})'
                    process = subprocess.Popen([sys.executable,'-c',source], start_new_session=True)
                    try:
                        if mode == 'success':
                            smoke.wait_for_smoke(process, marker, 'fixture', 1, 0.2)
                        else:
                            with self.assertRaises((RuntimeError, TimeoutError)):
                                smoke.wait_for_smoke(process, marker, 'fixture', 0.4, 0.2)
                    finally:
                        smoke.stop(process)
                    self.assertIsNotNone(process.poll())


if __name__ == '__main__':
    unittest.main()

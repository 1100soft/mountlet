"""Bounded installed-DMG verification; retain diagnostics before stopping a hung app."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def contains_bundled_rclone(root):
    return any(path.is_file() for path in Path(root).rglob('rclone'))


def wait_for_smoke(process, marker, version, ready_seconds=60, exit_seconds=10):
    deadline = time.monotonic() + ready_seconds
    while not marker.exists():
        if process.poll() is not None:
            if marker.exists():
                break
            raise RuntimeError(f'App exited {process.returncode} before writing startup marker')
        if time.monotonic() >= deadline:
            raise TimeoutError(f'App did not write startup marker within {ready_seconds}s')
        time.sleep(0.1)
    result = json.loads(marker.read_text())
    print('Startup marker:', json.dumps(result), flush=True)
    for key in ['buildId', 'frontendReady', 'mainWindowReady', 'mainWindowVisible', 'remoteStateReady', 'behaviorComplete']:
        if not result.get(key):
            raise RuntimeError(f'Incomplete startup marker: {key}')
    if result.get('version') != version:
        raise RuntimeError(f'Expected {version}, got {result.get("version")}')
    try:
        code = process.wait(timeout=exit_seconds)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f'App wrote readiness but did not exit within {exit_seconds}s') from error
    if code:
        raise RuntimeError(f'App verification exited with {code}')


def stop(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dmg')
    parser.add_argument('--bundled', choices=['true', 'false'], required=True)
    args = parser.parse_args()
    diagnostics = Path('artifacts/diagnostics')
    diagnostics.mkdir(parents=True, exist_ok=True)
    marker = diagnostics.resolve() / 'macos-startup.json'
    marker.unlink(missing_ok=True)
    version = json.loads(Path('package.json').read_text())['version']
    process = None
    mounted = False
    mountpoint = tempfile.mkdtemp(prefix='mountlet-smoke-')
    code = 0
    try:
        print('Attaching installed DMG (60s bound)', flush=True)
        subprocess.run(['hdiutil', 'attach', '-nobrowse', '-readonly', '-mountpoint', mountpoint, args.dmg], check=True, timeout=60)
        mounted = True
        executable = next(Path(mountpoint).glob('*.app/Contents/MacOS/mountlet'))
        bundled = contains_bundled_rclone(mountpoint)
        if bundled != (args.bundled == 'true'):
            raise RuntimeError('DMG rclone contents disagree with requested variant')
        output = subprocess.check_output([str(executable), '--version'], text=True, timeout=10).strip()
        if output != f'Mountlet {version}':
            raise RuntimeError(f'Invalid installed version: {output}')
        with open(diagnostics / 'macos-app.log', 'w') as log:
            print('Starting installed app: readiness <=60s, exit <=10s', flush=True)
            process = subprocess.Popen([str(executable)], env={**os.environ, 'MOUNTLET_STARTUP_SMOKE': str(marker)}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            wait_for_smoke(process, marker, version)
    except Exception as error:
        code = 1
        print(f'::error::macOS installed verification failed: {error}', flush=True)
        if process and process.poll() is None:
            try:
                subprocess.run(['sample', str(process.pid), '3', '-file', str(diagnostics / 'macos-process-sample.txt')], timeout=8, check=False)
            except (OSError, subprocess.TimeoutExpired) as diagnostic_error:
                print(f'Process sampling failed: {diagnostic_error}')
        with open(diagnostics / 'macos-processes.txt', 'w') as output:
            # Do not retain arbitrary runner command-line arguments/credentials.
            subprocess.run(['ps', '-axo', 'pid,ppid,stat,etime,comm'], stdout=output, timeout=5, check=False)
    finally:
        stop(process)
        log = diagnostics / 'macos-app.log'
        if log.exists():
            print('Last installed-app log lines:\n' + '\n'.join(log.read_text(errors='replace').splitlines()[-60:]))
        try:
            if mounted:
                subprocess.run(['hdiutil', 'detach', mountpoint], check=True, timeout=20)
            Path(mountpoint).rmdir()
        except (OSError, subprocess.SubprocessError) as error:
            print(f'::error::DMG cleanup failed: {error}')
            code = 1
    return code


if __name__ == '__main__':
    sys.exit(main())

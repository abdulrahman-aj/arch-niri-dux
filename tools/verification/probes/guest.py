"""Guest acceptance behavior."""

import json
import os
from pathlib import Path
import pwd
import selectors
import signal
import stat
import subprocess
import sys
import time
import urllib.error
from . import dispatch
from tools.verification.images import png_ready


def wait_until(description, check, timeout=60):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, subprocess.CalledProcessError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(0.5)
    raise TimeoutError(f'{description} did not become ready. Last error: {last_error}')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


EVIDENCE = Path('/run/verify')


def record(name, value):
    (EVIDENCE / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')


def output(*args, **kwargs):
    # Probe failures surface on the serial console; print the captured output
    # before re-raising so the log shows what the command actually said.
    try:
        return run(*args, stdout=subprocess.PIPE, **kwargs).stdout.strip()
    except subprocess.CalledProcessError as error:
        print(error.stdout, flush=True)
        raise


def run_interactive(command, replies, timeout=180):
    pending = list(replies)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    buffered = b''
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'Interactive command stalled: {command}')
                if not selector.select(min(remaining, 0.25)):
                    continue
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                print(chunk.decode(errors='replace'), end='', flush=True)
                buffered += chunk
                if pending and pending[0][0] in buffered:
                    prompt, answer = pending.pop(0)
                    buffered = buffered.split(prompt, 1)[1]
                    # A fresh upstream BufferedReader per prompt can discard
                    # prefetched answers. Send only the current answer.
                    process.stdin.write(answer)
                    process.stdin.flush()
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code or pending:
                raise RuntimeError(f'Interactive command failed ({code}); unanswered prompts: {pending}')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        process.stdin.close()
        process.stdout.close()


def _run_with_timeout_evidence(command, capture, timeout, on_timeout):
    # subprocess.run kills the parent before raising TimeoutExpired. Keep it
    # alive while the callback records which part of session/IPC setup stalled.
    with subprocess.Popen(command, text=True, stdout=subprocess.PIPE if capture else None) as process:
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                on_timeout(process.pid)
            except Exception as error:
                print(f'Timeout evidence collection failed: {type(error).__name__}: {error}', flush=True)
            finally:
                process.kill()
                process.wait()
            raise
        except BaseException:
            process.kill()
            process.wait()
            raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout)
    result.check_returncode()
    return result


def user_command(*args, capture=False, timeout=None, on_timeout=None):
    user = pwd.getpwnam('tester')
    runtime = Path(f'/run/user/{user.pw_uid}')
    wayland = next((path for path in runtime.glob('wayland-*') if stat.S_ISSOCK(path.stat().st_mode)), None)
    if wayland is None:
        raise RuntimeError('No Wayland socket found for the graphical test user.')
    niri = next(runtime.glob('niri.*.sock'))
    command = [
        'runuser',
        '-u',
        'tester',
        '--',
        'env',
        f'HOME={user.pw_dir}',
        f'XDG_CONFIG_HOME={user.pw_dir}/.config',
        f'XDG_CACHE_HOME={user.pw_dir}/.cache',
        f'XDG_DATA_HOME={user.pw_dir}/.local/share',
        f'XDG_STATE_HOME={user.pw_dir}/.local/state',
        'XDG_SESSION_TYPE=wayland',
        'XDG_CURRENT_DESKTOP=niri',
        f'NIRI_SOCKET={niri}',
        f'XDG_RUNTIME_DIR={runtime}',
        f'DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus',
        f'WAYLAND_DISPLAY={wayland.name}',
        *args,
    ]
    if on_timeout is not None:
        return _run_with_timeout_evidence(command, capture, timeout, on_timeout)
    return run(*command, stdout=subprocess.PIPE if capture else None, timeout=timeout)


def ipc(application, method, *parameters):
    return json.loads(user_command(application, 'ipc', method, *parameters, capture=True).stdout)


def windows():
    return json.loads(user_command('niri', 'msg', '--json', 'windows', capture=True).stdout)


def focus_window(window_id):
    user_command('niri', 'msg', 'action', 'focus-window', '--id', str(window_id))


def screenshot(name):
    path = EVIDENCE / name
    path.unlink(missing_ok=True)
    user_command('niri', 'msg', 'action', 'screenshot-screen', '--path', str(path))
    wait_until('Desktop screenshot', lambda: png_ready(path), timeout=10)


if __name__ == '__main__':
    dispatch('guest', {'screenshot': lambda: screenshot(sys.argv[2])})

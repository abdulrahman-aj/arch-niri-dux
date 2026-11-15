"""Login and graphical interaction for verification VMs."""

import csv
import shlex
import struct
import subprocess
import time
from tools.verification.constants import PASSWORD
from tools.verification.payload import probe


def wait_for_password_screen(vm, name):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        path = vm.screenshot(name)
        if path is not None:
            text = subprocess.check_output(
                ['tesseract', str(path), 'stdout', '--psm', '11'], text=True, stderr=subprocess.DEVNULL
            )
            if 'passphrase' in text.lower():
                path.with_suffix('.txt').write_text(text)
                return
        time.sleep(1)
    raise TimeoutError('The disk-password prompt did not appear on screen.')


def click_screen_text(vm, path, phrase):
    # Chromium's share chooser can appear later or move with window geometry.
    # Locate its visible label instead of clicking a guessed screen coordinate.
    output = subprocess.check_output(
        ['tesseract', str(path), 'stdout', '--psm', '11', 'tsv'], text=True, stderr=subprocess.DEVNULL
    )
    words = [row for row in csv.DictReader(output.splitlines(), delimiter='\t') if row.get('text', '').strip()]
    target = phrase.casefold().split()
    width, height = struct.unpack('>II', path.read_bytes()[16:24])
    for index in range(len(words) - len(target) + 1):
        match = words[index : index + len(target)]
        if [word['text'].casefold() for word in match] != target:
            continue
        if len({(word['block_num'], word['par_num'], word['line_num']) for word in match}) != 1:
            continue
        left = min(int(word['left']) for word in match)
        right = max(int(word['left']) + int(word['width']) for word in match)
        top = min(int(word['top']) for word in match)
        bottom = max(int(word['top']) + int(word['height']) for word in match)
        vm.click((left + right) / 2 / width, (top + bottom) / 2 / height)
        return True
    return False


def installed_login(vm, keyboard_unlock=False, screenshot_tag=None):
    if keyboard_unlock:
        wait_for_password_screen(vm, screenshot_tag + '-prompt')
        vm.type_graphical('wrong-passphrase-retry')
        vm.key('ret')
        # Let LUKS finish its intentionally expensive password check, then
        # require the password screen again before attempting the correct key.
        time.sleep(3)
        wait_for_password_screen(vm, screenshot_tag + '-retry')
        vm.type_graphical(PASSWORD)
        vm.key('ret')
    else:
        vm.expect(r'(?i)(passphrase|password).*?:', timeout=180)
        if screenshot_tag is not None:
            wait_for_password_screen(vm, screenshot_tag)
        vm.send(PASSWORD + '\n')
    vm.expect(r'login:', timeout=180)
    vm.send('tester\n')
    vm.expect(r'Password:')
    vm.send(PASSWORD + '\n')
    # Login/PAM can flush queued input while restoring the terminal. Both
    # Bash and Fish enable bracketed paste when their interactive reader starts.
    input_ready = r'\x1b\[\?2004h'
    vm.expect(input_ready, timeout=60)
    vm.command('test "$(id -u)" = 1000')
    vm.expect(input_ready, timeout=60)
    # Personal setup may change the login shell or grant passwordless sudo.
    # Completion markers establish readiness without depending on either prompt.
    vm.command('printf "%s\\n" ' + shlex.quote(PASSWORD) + ' | sudo -S -p "" -v')
    vm.expect(input_ready, timeout=60)
    vm.send('sudo -n bash --noprofile --norc\n')
    vm.expect(input_ready, timeout=60)
    vm.command('test "$(id -u)" = 0')


def graphical_login(vm):
    probe(vm, 'desktop', 'greeter')
    # Pinned upstream's first-login user picker, on the VM's single display.
    vm.click(0.5, 0.5)
    time.sleep(0.3)
    vm.click(0.5, 0.443)
    time.sleep(0.3)
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.command(
        'for attempt in $(seq 1 60); do pgrep -u tester -x niri >/dev/null && exit 0; sleep 1; done; exit 1', timeout=90
    )

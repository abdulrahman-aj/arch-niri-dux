import importlib.util
import json
import re
import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUEST_SPEC = importlib.util.spec_from_file_location('plymouth_guest', ROOT / 'scripts/verify-guest.py')
guest = importlib.util.module_from_spec(GUEST_SPEC)
GUEST_SPEC.loader.exec_module(guest)
VERIFY_SPEC = importlib.util.spec_from_file_location('plymouth_verify', ROOT / 'scripts/verify.py')
verify = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(verify)
LOCK_SPEC = importlib.util.spec_from_file_location('plymouth_lock', ROOT / 'scripts/build-lock.py')
lock_module = importlib.util.module_from_spec(LOCK_SPEC)
LOCK_SPEC.loader.exec_module(lock_module)


def configure_target():
    return (ROOT / 'installer/configure-target.sh').read_text()


def hooks():
    text = configure_target()
    match = re.search(r'^HOOKS=\((.*)\)', text, re.M)
    assert match, 'HOOKS missing from configure-target.sh'
    return match.group(1).split()


def layout(root, hook_line):
    (root / 'etc').mkdir(parents=True, exist_ok=True)
    (root / 'usr/lib/systemd/system').mkdir(parents=True, exist_ok=True)
    for theme in ('bgrt', 'spinner', 'details', 'text', 'tribar'):
        (root / f'usr/share/plymouth/themes/{theme}').mkdir(parents=True, exist_ok=True)
    for unit in ('plymouth-start.service', 'plymouth-quit.service', 'plymouth-quit-wait.service'):
        (root / f'usr/lib/systemd/system/{unit}').touch(exist_ok=True)
    (root / 'etc/mkinitcpio.conf').write_text(hook_line + '\n')
    (root / 'etc/kernel').mkdir(parents=True, exist_ok=True)
    (root / 'etc/kernel/cmdline').write_text(
        'rd.luks.name=uuid=dux-root root=/dev/mapper/dux-root rootflags=subvol=@ rw quiet splash\n')
    (root / 'etc/plymouth').mkdir(parents=True, exist_ok=True)
    (root / 'etc/plymouth/plymouthd.conf').write_text('[Daemon]\nTheme=bgrt\n')
    dropin = root / 'etc/systemd/system/greetd.service.d'
    dropin.mkdir(parents=True, exist_ok=True)
    (dropin / '10-plymouth.conf').write_text(
        '[Unit]\nAfter=plymouth-quit.service plymouth-quit-wait.service\n')


class FakeLoginVM:
    """Minimal serial terminal: re-prompts once, then follows installed_login."""

    def __init__(self):
        self.events = []
        self.stage = 0

    def expect(self, pattern, timeout=120):
        self.events.append(('expect', pattern))
        # Stages: luks prompt, optional retry prompt, login, bracketed paste x2, sudo marker.
        self.stage += 1
        return None, ''

    def send(self, value):
        self.events.append(('send', value))

    def screenshot(self, name):
        self.events.append(('screenshot', name))

    def command(self, command, timeout=120):
        self.events.append(('command', command))
        return ''

    def sends(self):
        return [value for kind, value in self.events if kind == 'send']

    def screenshots(self):
        return [value for kind, value in self.events if kind == 'screenshot']


class PlymouthBootTests(unittest.TestCase):
    def test_target_package_list_includes_plymouth(self):
        packages = [line.split('#')[0].strip()
                    for line in (ROOT / 'packages/packages.target').read_text().splitlines()]
        self.assertIn('plymouth', packages)

    def test_closure_adds_only_signed_plymouth_and_preserves_existing_pins(self):
        payload = json.loads((ROOT / 'packages/closure.lock.json').read_text())
        self.assertIn('plymouth', payload['requested'])
        self.assertEqual(payload['requested'], sorted(payload['requested']))
        self.assertEqual([e['name'] for e in payload['packages']],
                         sorted(e['name'] for e in payload['packages']))
        entry = next(e for e in payload['packages'] if e['name'] == 'plymouth')
        lock_module.validate_entry(entry)
        self.assertEqual(entry['repository'], 'extra')
        previous = subprocess.check_output(
            ['git', 'show', 'HEAD:packages/closure.lock.json'], text=True, cwd=ROOT)
        old = json.loads(previous)
        self.assertEqual(len(payload['packages']), len(old['packages']) + 1)
        old_by_name = {e['name']: e for e in old['packages']}
        for current in payload['packages']:
            if current['name'] == 'plymouth':
                continue
            self.assertEqual(current, old_by_name[current['name']])
        names = {e['name'] for e in payload['packages']}
        for dependency in ('adwaita-fonts', 'cairo', 'pango', 'libdrm', 'systemd-libs'):
            self.assertIn(dependency, names)

    def test_hooks_keep_systemd_before_plymouth_before_sd_encrypt(self):
        ordered = hooks()
        for hook in ('systemd', 'kms', 'plymouth', 'sd-encrypt'):
            self.assertIn(hook, ordered)
        self.assertLess(ordered.index('systemd'), ordered.index('plymouth'))
        self.assertLess(ordered.index('plymouth'), ordered.index('sd-encrypt'))

    def test_cmdline_keeps_luks_resume_and_adds_quiet_splash(self):
        text = configure_target()
        self.assertIn('rd.luks.name=', text)
        self.assertIn('quiet splash', text)
        self.assertIn('resume=/dev/mapper/dux-root resume_offset=', text)
        self.assertIn('SNAPSHOT_KERNEL_PARAMETERS+=noresume', text)

    def test_stock_theme_and_greeter_ordering_without_custom_branding(self):
        text = configure_target()
        self.assertIn('plymouth-set-default-theme bgrt', text)
        self.assertNotIn('background-tile', text)
        self.assertIn('greetd.service.d/10-plymouth.conf', text)
        self.assertIn('After=plymouth-quit.service', text)
        self.assertIn('limine-mkinitcpio', text)

    def test_guest_plymouth_config_accepts_expected_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout(root, 'HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block plymouth sd-encrypt filesystems sd-btrfs-overlayfs fsck)')
            guest.plymouth_config(root)

    def test_guest_plymouth_config_rejects_wrong_hook_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout(root, 'HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block sd-encrypt plymouth filesystems fsck)')
            with self.assertRaises(AssertionError):
                guest.plymouth_config(root)

    def test_guest_plymouth_config_rejects_missing_splash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout(root, 'HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block plymouth sd-encrypt filesystems fsck)')
            (root / 'etc/kernel/cmdline').write_text(
                'rd.luks.name=uuid=dux-root root=/dev/mapper/dux-root rw\n')
            with self.assertRaises(AssertionError):
                guest.plymouth_config(root)

    def test_splash_fallback_toggle_preserves_quiet_splash_and_luks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'etc/kernel').mkdir(parents=True)
            (root / 'etc/default').mkdir(parents=True)
            base = ('rd.luks.name=uuid=dux-root root=/dev/mapper/dux-root '
                    'rootflags=subvol=@ rw quiet splash resume=/dev/mapper/dux-root resume_offset=99')
            (root / 'etc/kernel/cmdline').write_text(base + '\n')
            (root / 'etc/default/limine').write_text(f'KERNEL_CMDLINE[default]="{base}"\n')
            guest.splash_fallback(True, root)
            disabled = (root / 'etc/kernel/cmdline').read_text().split()
            self.assertIn('plymouth.enable=0', disabled)
            self.assertIn('quiet', disabled)
            self.assertIn('splash', disabled)
            self.assertIn('resume=/dev/mapper/dux-root', disabled)
            self.assertIn('plymouth.enable=0', (root / 'etc/default/limine').read_text())
            # Disabling twice stays idempotent.
            guest.splash_fallback(True, root)
            self.assertEqual((root / 'etc/kernel/cmdline').read_text().split().count('plymouth.enable=0'), 1)
            guest.splash_fallback(False, root)
            enabled = (root / 'etc/kernel/cmdline').read_text().split()
            self.assertNotIn('plymouth.enable=0', enabled)
            self.assertIn('quiet', enabled)
            self.assertIn('splash', enabled)
            self.assertNotIn('plymouth.enable=0', (root / 'etc/default/limine').read_text())

    def test_wrong_passphrase_retries_then_unlocks_with_screenshots(self):
        vm = FakeLoginVM()
        verify.installed_login(vm, wrong_first=True, screenshot_tag='02-boot')
        sends = vm.sends()
        self.assertIn('wrong-passphrase-retry\n', sends)
        self.assertIn(verify.PASSWORD + '\n', sends)
        self.assertLess(sends.index('wrong-passphrase-retry\n'), sends.index(verify.PASSWORD + '\n'))
        shots = vm.screenshots()
        self.assertIn('02-boot-plymouth-prompt', shots)
        self.assertIn('02-boot-plymouth-retry', shots)

    def test_default_login_path_sends_no_wrong_passphrase(self):
        vm = FakeLoginVM()
        verify.installed_login(vm)
        self.assertNotIn('wrong-passphrase-retry\n', vm.sends())
        self.assertEqual(vm.screenshots(), [])


if __name__ == '__main__':
    unittest.main()

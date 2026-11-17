"""NVIDIA early-KMS policy: the mkinitcpio rule over fake PCI trees, and apply-nvidia's link."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.fake_commands import mark


ROOT = Path(__file__).resolve().parents[1]
POLICY = '/usr/share/arch-niri-dux/mkinitcpio-nvidia.conf'
BASE_CONFIG = ROOT / 'desktop/etc/mkinitcpio.conf.d/arch-niri-dux.conf'
APPLY = ROOT / 'desktop/usr/lib/arch-niri-dux/apply-nvidia'
NVIDIA_MODULES = ['nvidia', 'nvidia_modeset', 'nvidia_uvm', 'nvidia_drm']
DISPLAY = '0x030000'
AUDIO = '0x040300'


class MkinitcpioPolicyTests(unittest.TestCase):
    """Source the drop-ins in mkinitcpio's order and read back MODULES and HOOKS."""

    def build(self, devices):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            for index, (vendor, device_class) in enumerate(devices):
                device = tree / f'0000:0{index}:00.0'
                device.mkdir()
                if vendor is not None:
                    (device / 'vendor').write_text(vendor + '\n')
                    (device / 'class').write_text(device_class + '\n')
            script = (
                'set -eu; source "$1"; source "$2"; '
                'printf "%s\\n" "${MODULES[*]}" "${HOOKS[*]}"; '
                # The policy must leave no temporaries in mkinitcpio's shell.
                'compgen -v _and_ || true'
            )
            result = subprocess.run(
                ['bash', '-c', script, 'policy', str(BASE_CONFIG), str(ROOT / 'desktop' / POLICY.lstrip('/'))],
                env=os.environ | {'ARCH_NIRI_DUX_PCI_DEVICES': str(tree)},
                capture_output=True,
                text=True,
                check=True,
            )
        modules, hooks, *leftovers = result.stdout.splitlines()
        self.assertEqual(leftovers, [])
        self.assertEqual(modules.split(), ['btrfs', *NVIDIA_MODULES])
        return hooks.split()

    def test_nvidia_only_machine_drops_kms(self):
        hooks = self.build([('0x10de', DISPLAY), ('0x10de', AUDIO), ('0x8086', '0x060000')])
        self.assertNotIn('kms', hooks)
        self.assertIn('plymouth', hooks)

    def test_hybrid_machine_keeps_kms_for_the_integrated_gpu(self):
        self.assertIn('kms', self.build([('0x8086', DISPLAY), ('0x10de', '0x030200')]))

    def test_machine_without_its_nvidia_card_keeps_kms(self):
        self.assertIn('kms', self.build([('0x1af4', DISPLAY)]))

    def test_unreadable_device_keeps_kms(self):
        self.assertIn('kms', self.build([('0x10de', DISPLAY), (None, None)]))


class ApplyNvidiaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / 'root'
        (self.root / 'etc/mkinitcpio.conf.d').mkdir(parents=True)
        self.link = self.root / 'etc/mkinitcpio.conf.d/nvidia.conf'
        self.bin = self.directory / 'bin'
        self.bin.mkdir()

    def apply(self, driver_installed):
        # pacman -T exits 0 when the dependency is satisfied, 127 when not.
        pacman = self.bin / 'pacman'
        pacman.write_text(
            f'#!/bin/sh\n[ "$1 $2" = "-T NVIDIA-MODULE" ] || exit 2\nexit {0 if driver_installed else 127}\n'
        )
        pacman.chmod(0o755)
        marker = mark(pacman, self.directory / 'pacman-ran')
        marker.unlink(missing_ok=True)
        subprocess.run(
            ['sh', str(APPLY)],
            env=os.environ
            | {'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}', 'ARCH_NIRI_DUX_ROOT': str(self.root)},
            check=True,
        )
        self.assertTrue(marker.exists())

    def test_links_while_a_driver_is_installed_and_unlinks_after_removal(self):
        self.apply(driver_installed=True)
        self.assertEqual(os.readlink(self.link), POLICY)
        self.apply(driver_installed=True)
        self.assertEqual(os.readlink(self.link), POLICY)
        self.apply(driver_installed=False)
        self.assertFalse(self.link.is_symlink())

    def test_without_a_driver_nothing_is_linked(self):
        self.apply(driver_installed=False)
        self.assertFalse(self.link.is_symlink())

    def test_a_users_own_file_is_never_replaced_or_removed(self):
        self.link.write_text('MODULES+=(nvidia)\n')
        for installed in (True, False):
            self.apply(driver_installed=installed)
            self.assertFalse(self.link.is_symlink())
            self.assertEqual(self.link.read_text(), 'MODULES+=(nvidia)\n')

    def test_a_users_own_link_is_never_replaced_or_removed(self):
        self.link.symlink_to('/etc/custom-nvidia.conf')
        for installed in (True, False):
            self.apply(driver_installed=installed)
            self.assertEqual(os.readlink(self.link), '/etc/custom-nvidia.conf')


if __name__ == '__main__':
    unittest.main()

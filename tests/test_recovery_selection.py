import unittest

from scripts.verification import recovery as guest

MENU = '''timeout: 5
default_entry: 2
/+Arch Niri Dux
comment: machine-id=fixture
//linux
protocol: linux
kernel_cmdline: root=/dev/mapper/dux-root rootflags=subvol=@ rw
//+Snapshots
///Snapshot 10
protocol: linux
kernel_cmdline: rootflags=subvol=/@snapshots/10/snapshot rw
///Snapshot 1
protocol: linux
kernel_cmdline: rootflags=subvol=/@snapshots/1/snapshot rw
'''


class RecoveryMenuTests(unittest.TestCase):
    def test_selects_exact_snapshot_instead_of_prefix_match(self):
        self.assertEqual(guest.boot_entry_path(MENU, '1'), 'Arch Niri Dux/Snapshots/Snapshot 1')

    def test_normal_entry_does_not_select_a_snapshot(self):
        self.assertEqual(guest.boot_entry_path(MENU), 'Arch Niri Dux/linux')

    def test_missing_snapshot_fails_instead_of_booting_normal_root(self):
        with self.assertRaises(RuntimeError):
            guest.boot_entry_path(MENU, '2')

    def test_escapes_special_menu_title_characters(self):
        menu = MENU.replace('Snapshot 1\n', 'Snapshot #1 / recovery\n')
        self.assertEqual(guest.boot_entry_path(menu, '1'), 'Arch Niri Dux/Snapshots/Snapshot \\#1 \\/ recovery')


if __name__ == '__main__':
    unittest.main()

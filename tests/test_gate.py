"""Unit tests for the verification error gate (normalize, allowlist, check)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verification import gate


class NormalizeTests(unittest.TestCase):
    def test_strips_ansi_escapes(self):
        self.assertEqual(gate.normalize('\x1b[32mok\x1b[0m'), 'ok')

    def test_syslog_timestamp_keeps_date(self):
        self.assertEqual(
            gate.normalize('Nov 12 20:43:17 dux-verify systemd[748]: dsearch.service: boom'),
            'Nov 12 <TS> dux-verify systemd[<PID>]: dsearch.service: boom',
        )

    def test_iso_timestamp(self):
        self.assertEqual(
            gate.normalize('2026-11-10T21:07:54.913845Z  WARN niri_config: x'),
            '<TS>  WARN niri_config: x',
        )

    def test_kernel_timestamp(self):
        self.assertEqual(gate.normalize('[    0.156704] virt/tdx: TDX nope'), '[<TS>] virt/tdx: TDX nope')

    def test_bare_time_and_pid_and_number(self):
        self.assertEqual(
            gate.normalize('(org.gnome.Decibels:4124): Gjs-WARNING **: 21:08:12.376: Value 18446744073709551615 x'),
            '(org.gnome.Decibels:<PID>): Gjs-WARNING **: <TS>: Value <NUM> x',
        )

    def test_hex_uuid_hash_and_qml_lines(self):
        self.assertEqual(
            gate.normalize('PciRoot(0x0)/Pci(0x3,0x0) bootid=97913a06-3004-4613-ab6b-6b67431b2f85 file.qml:1289:21'),
            'PciRoot(<HEX>)/Pci(<HEX>,<HEX>) bootid=<UUID> file.qml:<LINE>',
        )

    def test_small_numbers_and_versions_stay_literal(self):
        line = 'restart counter is at 5; mesa-26.2.6; 99999999_03_verification.sh failed'
        self.assertEqual(gate.normalize(line), line)


class AllowlistTests(unittest.TestCase):
    def test_seed_entries_match_current_guest_lines(self):
        entries = gate.load_allowlist()
        samples = [
            'Nov 12 20:43:10 archlinux kernel: virt/tdx: TDX not supported by the host platform',
            "Nov 12 20:43:17 dux-verify dbus-broker-launch[665]: Activation request for 'org.freedesktop.home1'"
            " failed: The systemd unit 'dbus-org.freedesktop.home1.service' could not be found.",
            'Nov 12 20:43:17 dux-verify dbus-broker-launch[766]: Ignoring duplicate name'
            " 'org.freedesktop.FileManager1' in service file"
            " '/usr/share/dbus-1/services/org.freedesktop.FileManager1.service'",
            'Nov 12 20:43:17 dux-verify pipewire[829]: mod.rt: RTKit error: org.freedesktop.DBus.Error.ServiceUnknown',
            'Nov 12 20:43:17 dux-verify wireplumber[830]: spa.bluez5: BlueZ system service is not available',
            'Nov 12 23:28:12 dux-verify /usr/lib/xdg-desktop-portal[1118]:'
            " g_value_unset: assertion 'value_table' failed",
            'Nov 12 23:28:13 dux-verify accounts-daemon[1134]: Unable to open /etc/tcb: No such file or directory',
            "Nov 12 20:43:17 dux-verify systemd[734]: dsearch.service: Failed with result 'exit-code'.",
            "Nov 12 20:43:17 dux-verify greetd[725]: gkr-pam: couldn't unlock the login keyring.",
        ]
        for raw in samples:
            normalized = gate.normalize(raw)
            self.assertTrue(
                any(pattern.search(normalized) for pattern, _ in entries), f'no allowlist entry for {normalized!r}'
            )

    def test_retired_probe_lines_are_not_allowlisted(self):
        # The 99999999 migration fixtures are gone from current probes; if such
        # a line ever reappears the gate must fail rather than silently pass.
        entries = gate.load_allowlist()
        normalized = gate.normalize(
            'Nov 10 21:10:40 dux-verify arch-niri-dux-migrate[1513]:'
            ' 99999999_03_verification.sh failed (exit 1); retry with arch-niri-dux-migrate run'
        )
        self.assertFalse(any(pattern.search(normalized) for pattern, _ in entries))

    def test_malformed_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'allowlist.txt'
            path.write_text('unanchored reason without separator\n')
            with self.assertRaises(ValueError):
                gate.load_allowlist(path)


class CheckTests(unittest.TestCase):
    def write_evidence(self, root, journal=(), failed=(), failed_user=()):
        directory = Path(root) / 'evidence'
        directory.mkdir()
        (directory / gate.JOURNAL_FILE).write_text('\n'.join(journal) + '\n')
        (directory / gate.FAILED_FILES[0]).write_text('\n'.join(failed) + '\n')
        (directory / gate.FAILED_FILES[1]).write_text('\n'.join(failed_user) + '\n')
        return directory

    def test_clean_boot_passes(self):
        with tempfile.TemporaryDirectory() as root:
            directory = self.write_evidence(
                root,
                journal=[
                    'Nov 12 20:43:10 archlinux kernel: virt/tdx: TDX not supported by the host platform',
                    'Nov 12 20:43:17 dux-verify wireplumber[830]: spa.bluez5: BlueZ system service is not available',
                ],
            )
            self.assertEqual(gate.check(directory), [])

    def test_planted_error_fails_with_raw_and_normalized(self):
        with tempfile.TemporaryDirectory() as root:
            directory = self.write_evidence(
                root,
                journal=[
                    'Nov 12 20:43:10 archlinux kernel: virt/tdx: TDX not supported by the host platform',
                    'Nov 13 01:02:03 dux-verify planted[1234]: planted gate test',
                ],
            )
            with self.assertRaises(gate.GateFailure) as failure:
                gate.check(directory)
            message = str(failure.exception)
            self.assertIn('planted gate test', message)
            self.assertIn('planted[<PID>]: planted gate test', message)
            self.assertIn('guest-journal-warning.log', message)
            report = (directory / gate.UNMATCHED_FILE).read_text()
            self.assertIn('planted[1234]: planted gate test', report)

import configparser
import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify-guest.py'
spec = importlib.util.spec_from_file_location('app_defaults_probe', SCRIPT)
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / 'desktop/etc/xdg/mimeapps.list'

# Desktop IDs must match the factory file and official Arch package files.
EXECS = {
    'dev.duxlabs.files.desktop': 'dux-files-fake',
    'org.gnome.Papers.desktop': 'papers-fake',
    'chromium.desktop': 'chromium-fake',
    'org.gnome.TextEditor.desktop': 'gnome-text-editor-fake',
    'org.gnome.Loupe.desktop': 'loupe-fake',
    'io.github.celluloid_player.Celluloid.desktop': 'celluloid-fake',
    'org.gnome.Decibels.desktop': 'decibels-fake',
    'Other.desktop': 'other-fake',
}


def find_xdg_mime():
    found = shutil.which('xdg-mime')
    if found:
        return found
    raise RuntimeError('Run tests through the project Nix dev shell for xdg-mime.')


def read_factory_defaults():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(FACTORY, encoding='utf-8')
    return dict(parser['Default Applications'])


class SamplePdfTests(unittest.TestCase):
    def test_generated_pdf_xref_offsets_are_valid(self):
        data = guest.sample_pdf_bytes()
        self.assertTrue(data.startswith(b'%PDF-1.4\n'))
        self.assertIn(b'Dux verification PDF works', data)
        self.assertTrue(data.rstrip().endswith(b'%%EOF'))
        lines = data.splitlines()
        xref = lines.index(b'xref')
        count = int(lines[xref + 1].split()[1])
        self.assertEqual(count, 6)
        for index in range(1, count):
            offset = int(lines[xref + 2 + index].split()[0])
            self.assertTrue(data[offset:].startswith(b'%d 0 obj' % index))


class FactoryMimeTests(unittest.TestCase):
    def setUp(self):
        self.binary = find_xdg_mime()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.system = self.root / 'system'
        self.user = self.root / 'user'
        self.data_home = self.root / 'data-home'
        self.data_dirs = self.root / 'data-dirs'
        self.apps = self.data_home / 'applications'
        self.bin = self.root / 'bin'
        for directory in (self.system, self.user, self.apps, self.data_dirs, self.bin):
            directory.mkdir(parents=True)
        shutil.copyfile(FACTORY, self.system / 'mimeapps.list')
        for desktop, binary in EXECS.items():
            executable = self.bin / binary
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o755)
            (self.apps / desktop).write_text(
                '[Desktop Entry]\nType=Application\n'
                f'Name={desktop}\nExec={binary} %U\n')
        self.environment = os.environ | {
            'HOME': str(self.root),
            'PATH': str(self.bin) + ':' + os.environ['PATH'],
            'XDG_CONFIG_HOME': str(self.user),
            'XDG_CONFIG_DIRS': str(self.system),
            'XDG_DATA_HOME': str(self.data_home),
            'XDG_DATA_DIRS': str(self.data_dirs),
            'XDG_CACHE_HOME': str(self.root / 'cache'),
        }

    def query(self, mime):
        return subprocess.check_output(
            [self.binary, 'query', 'default', mime],
            env=self.environment, text=True).strip()

    def test_fresh_user_resolves_factory_defaults(self):
        expected = read_factory_defaults()
        self.assertGreater(len(expected), 10)
        for mime, desktop in expected.items():
            with self.subTest(mime=mime):
                self.assertEqual(self.query(mime), desktop)

    def test_existing_user_choice_wins_over_factory(self):
        (self.user / 'mimeapps.list').write_text(
            '[Default Applications]\n'
            'text/plain=Other.desktop\n'
            'image/jpeg=Other.desktop\n')
        self.assertEqual(self.query('text/plain'), 'Other.desktop')
        self.assertEqual(self.query('image/jpeg'), 'Other.desktop')
        self.assertEqual(self.query('text/markdown'), 'org.gnome.TextEditor.desktop')
        self.assertEqual(self.query('image/png'), 'org.gnome.Loupe.desktop')
        self.assertEqual(self.query('application/pdf'), 'org.gnome.Papers.desktop')

    def test_representative_fresh_defaults(self):
        self.assertEqual(self.query('inode/directory'), 'dev.duxlabs.files.desktop')
        self.assertEqual(self.query('application/pdf'), 'org.gnome.Papers.desktop')
        self.assertEqual(self.query('text/html'), 'chromium.desktop')
        self.assertEqual(self.query('text/plain'), 'org.gnome.TextEditor.desktop')
        self.assertEqual(self.query('text/markdown'), 'org.gnome.TextEditor.desktop')
        self.assertEqual(self.query('image/jpeg'), 'org.gnome.Loupe.desktop')
        self.assertEqual(self.query('image/png'), 'org.gnome.Loupe.desktop')
        self.assertEqual(self.query('video/mp4'), 'io.github.celluloid_player.Celluloid.desktop')
        self.assertEqual(self.query('audio/mpeg'), 'org.gnome.Decibels.desktop')
        self.assertEqual(self.query('audio/vnd.wave'), 'org.gnome.Decibels.desktop')
        self.assertEqual(self.query('video/matroska'), 'io.github.celluloid_player.Celluloid.desktop')
        self.assertEqual(self.query('video/vnd.avi'), 'io.github.celluloid_player.Celluloid.desktop')
        self.assertEqual(self.query('image/heif'), 'org.gnome.Loupe.desktop')


if __name__ == '__main__':
    unittest.main()

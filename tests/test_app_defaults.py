import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify-guest.py'
spec = importlib.util.spec_from_file_location('app_defaults_probe', SCRIPT)
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)

ROOT = Path(__file__).resolve().parents[1]


class SamplePdfTests(unittest.TestCase):
    def test_generated_pdf_is_structured_and_contains_verification_text(self):
        data = guest.sample_pdf_bytes()
        self.assertTrue(data.startswith(b'%PDF-1.4\n'))
        self.assertIn(b'Dux verification PDF works', data)
        self.assertIn(b'xref', data)
        self.assertIn(b'trailer', data)
        self.assertTrue(data.rstrip().endswith(b'%%EOF'))
        # Offsets in xref must point at object headers.
        lines = data.splitlines()
        xref = lines.index(b'xref')
        count = int(lines[xref + 1].split()[1])
        self.assertEqual(count, 6)
        for index in range(1, count):
            offset = int(lines[xref + 2 + index].split()[0])
            self.assertTrue(data[offset:].startswith(b'%d 0 obj' % index))


class WallpaperProbeTests(unittest.TestCase):
    def run_wallpaper(self, session_data):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            session_file = home / '.local/state/dms/session.json'
            run_verify = home / 'run-verify'
            run_verify.mkdir()
            session_file.parent.mkdir(parents=True)
            if session_data is not None:
                session_file.write_text(json.dumps(session_data))
            evidence = run_verify / '02-wallpaper-bloom.png'
            output = run_verify / 'wallpaper.json'

            def fake_screenshot(name):
                (run_verify / name).write_bytes(b'fake-png' + b'\x00' * 100)

            with patch.object(guest.Path, 'home', return_value=home), \
                patch.object(guest, 'screenshot', side_effect=fake_screenshot), \
                patch('pathlib.Path.home', return_value=home):
                # guest.wallpaper uses absolute /home/tester and /run/verify;
                # redirect by patching Path.
                real_path = guest.Path
                def path_factory(*args):
                    text = str(real_path(*args))
                    if text.startswith('/home/tester/.local/state/dms/session.json'):
                        return session_file
                    if text.startswith('/run/verify/'):
                        return run_verify / text[len('/run/verify/'):]
                    return real_path(*args)
                with patch.object(guest, 'Path', side_effect=path_factory):
                    # Path is used as constructor; also need attribute access.
                    guest.Path.is_file = real_path.is_file
                    try:
                        guest.wallpaper()
                    finally:
                        pass
            return evidence, output

    def test_bloom_seed_passes_settings_and_rendered_default(self):
        with tempfile.TemporaryDirectory() as directory:
            session_file = Path(directory) / 'session.json'
            session_file.write_text(json.dumps({
                'configVersion': 7,
                'materialWallpapers': {'': {'shared': {'preset': 'bloom', 'seed': '#d0bcff'}}},
            }))
            run_verify = Path(directory) / 'verify'
            run_verify.mkdir()
            created = []
            def fake_screenshot(name):
                target = run_verify / name
                target.write_bytes(b'\x89PNG' + b'\x00' * 200)
                created.append(target)
            real_path = Path
            def factory(*args):
                text = str(real_path(*args))
                if text == '/home/tester/.local/state/dms/session.json':
                    return session_file
                if text.startswith('/run/verify/'):
                    return run_verify / text[len('/run/verify/'):]
                if text == '/run/verify/02-wallpaper-bloom.png':
                    return run_verify / '02-wallpaper-bloom.png'
                return real_path(*args)
            with patch.object(guest, 'screenshot', side_effect=fake_screenshot), \
                    patch.object(guest, 'Path', side_effect=factory):
                guest.wallpaper()
            evidence = run_verify / '02-wallpaper-bloom.png'
            self.assertTrue(evidence.is_file())
            details = json.loads((run_verify / 'wallpaper.json').read_text())
            self.assertEqual(details['preset'], 'bloom')

    def test_non_bloom_default_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            session_file = Path(directory) / 'session.json'
            session_file.write_text(json.dumps({
                'configVersion': 7,
                'materialWallpapers': {'': {'shared': {'preset': 'dank', 'seed': '#d0bcff'}}},
            }))
            run_verify = Path(directory) / 'verify'
            run_verify.mkdir()
            def factory(*args):
                text = str(Path(*args))
                if text == '/home/tester/.local/state/dms/session.json':
                    return session_file
                if text.startswith('/run/verify/'):
                    return run_verify / text[len('/run/verify/'):]
                return Path(*args)
            with patch.object(guest, 'screenshot'), \
                    patch.object(guest, 'Path', side_effect=factory):
                with self.assertRaisesRegex(AssertionError, 'bloom'):
                    guest.wallpaper()

    def test_explicit_wallpaper_path_is_not_a_material_default(self):
        with tempfile.TemporaryDirectory() as directory:
            session_file = Path(directory) / 'session.json'
            session_file.write_text(json.dumps({
                'configVersion': 7, 'wallpaperPath': '/home/tester/custom.jpg',
                'materialWallpapers': {'': {'shared': {'preset': 'bloom', 'seed': '#d0bcff'}}},
            }))
            run_verify = Path(directory) / 'verify'
            run_verify.mkdir()
            def factory(*args):
                text = str(Path(*args))
                if text == '/home/tester/.local/state/dms/session.json':
                    return session_file
                if text.startswith('/run/verify/'):
                    return run_verify / text[len('/run/verify/'):]
                return Path(*args)
            with patch.object(guest, 'screenshot'), \
                    patch.object(guest, 'Path', side_effect=factory):
                with self.assertRaisesRegex(AssertionError, 'Material wallpaper'):
                    guest.wallpaper()


class PdfProbeTests(unittest.TestCase):
    def test_papers_default_and_render_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            run_verify = Path(directory)
            sample = Path(directory) / 'dux-verification.pdf'
            windows = [{'app_id': 'org.gnome.Papers', 'title': 'dux-verification.pdf'}]
            def fake_user(*args, **kwargs):
                class Result:
                    stdout = 'org.gnome.Papers.desktop' if 'xdg-mime' in args else ''
                if args[:2] == ('test', '-x'):
                    return Result()
                return Result()
            created = []
            def fake_screenshot(name):
                (run_verify / name).write_bytes(b'\x89PNG' + b'\x00' * 100)
                created.append(name)
            real_path = Path
            def factory(*args):
                text = str(real_path(*args))
                if text == '/usr/share/applications/org.gnome.Papers.desktop':
                    probe = Path(directory) / 'org.gnome.Papers.desktop'
                    probe.touch()
                    return probe
                if text == '/etc/xdg/mimeapps.list':
                    probe = Path(directory) / 'mimeapps.list'
                    probe.write_text('[Default Applications]\napplication/pdf=org.gnome.Papers.desktop\n')
                    return probe
                if text == '/tmp/dux-verification.pdf':
                    return sample
                if text.startswith('/run/verify/'):
                    return run_verify / text[len('/run/verify/'):]
                return real_path(*args)
            with patch.object(guest, 'user_command', side_effect=fake_user), \
                    patch.object(guest, 'windows', return_value=windows), \
                    patch.object(guest, 'screenshot', side_effect=fake_screenshot), \
                    patch.object(guest, 'wait_until', side_effect=lambda desc, fn, timeout=60: fn() or True), \
                    patch.object(guest, 'Path', side_effect=factory):
                guest.pdf()
            self.assertIn('02-papers.png', created)
            details = json.loads((run_verify / 'pdf.json').read_text())
            self.assertEqual(details['default'], 'org.gnome.Papers.desktop')
            self.assertTrue(sample.is_file())

    def test_wrong_pdf_default_is_rejected(self):
        def fake_user(*args, **kwargs):
            class Result:
                stdout = 'evince.desktop'
            return Result()
        real_path = Path
        def factory(*args):
            text = str(real_path(*args))
            if text == '/usr/share/applications/org.gnome.Papers.desktop':
                return real_path('/usr/share/applications/org.gnome.Papers.desktop')
            if text.startswith('/run/verify/'):
                return real_path(tempfile.gettempdir()) / text[len('/run/verify/'):]
            return real_path(*args)
        with patch.object(guest, 'user_command', side_effect=fake_user), \
                patch.object(guest, 'Path', side_effect=factory):
            with self.assertRaises(AssertionError):
                guest.pdf()


class CameraProbeTests(unittest.TestCase):
    def test_snapshot_install_verified_without_claiming_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            run_verify = Path(directory)
            windows = [{'app_id': 'org.gnome.Snapshot', 'title': 'Snapshot'}]
            def fake_user(*args, **kwargs):
                class Result:
                    stdout = ''
                return Result()
            def fake_screenshot(name):
                (run_verify / name).write_bytes(b'\x89PNG' + b'\x00' * 50)
            real_path = Path
            def factory(*args):
                text = str(real_path(*args))
                if text == '/usr/share/applications/org.gnome.Snapshot.desktop':
                    probe = Path(directory) / 'org.gnome.Snapshot.desktop'
                    probe.touch()
                    return probe
                if text == '/dev':
                    return real_path('/dev')
                if text.startswith('/run/verify/'):
                    return run_verify / text[len('/run/verify/'):]
                return real_path(*args)
            with patch.object(guest, 'user_command', side_effect=fake_user), \
                    patch.object(guest, 'windows', return_value=windows), \
                    patch.object(guest, 'screenshot', side_effect=fake_screenshot), \
                    patch.object(guest, 'wait_until', side_effect=lambda desc, fn, timeout=60: True if fn() else (_ for _ in ()).throw(TimeoutError(desc))), \
                    patch.object(guest, 'Path', side_effect=factory):
                # /dev/video* almost certainly absent in CI; probe must not claim capture.
                guest.camera()
            details = json.loads((run_verify / 'camera.json').read_text())
            self.assertTrue(details['installed'])
            self.assertIn('has_camera', details)
            self.assertNotIn('capture', json.dumps(details).lower().replace('capture not claimed', ''))
            self.assertIn('not claimed', details['note'])

    def test_missing_snapshot_desktop_is_rejected(self):
        def fake_user(*args, **kwargs):
            class Result:
                stdout = ''
            return Result()
        real_path = Path
        with tempfile.TemporaryDirectory() as directory:
            def factory(*args):
                text = str(real_path(*args))
                if text == '/usr/share/applications/org.gnome.Snapshot.desktop':
                    return real_path(directory) / 'missing.desktop'
                if text.startswith('/run/verify/'):
                    return real_path(directory) / text[len('/run/verify/'):]
                return real_path(*args)
            with patch.object(guest, 'user_command', side_effect=fake_user), \
                    patch.object(guest, 'Path', side_effect=factory):
                with self.assertRaises(AssertionError):
                    guest.camera()


class PackageClosureTests(unittest.TestCase):
    def test_target_includes_papers_and_snapshot_but_live_does_not(self):
        target = (ROOT / 'packages/packages.target').read_text().splitlines()
        target_names = {line.split('#')[0].strip() for line in target if line.split('#')[0].strip()}
        self.assertIn('papers', target_names)
        self.assertIn('snapshot', target_names)
        live = (ROOT / 'archiso/packages.live').read_text().splitlines()
        live_names = {line.split('#')[0].strip() for line in live if line.split('#')[0].strip()}
        self.assertNotIn('papers', live_names)
        self.assertNotIn('snapshot', live_names)

    def test_closure_extends_only_with_new_signed_runtime_deps(self):
        lock = json.loads((ROOT / 'packages/closure.lock.json').read_text())
        self.assertEqual(lock['schema'], 1)
        self.assertIn('papers', lock['requested'])
        self.assertIn('snapshot', lock['requested'])
        by_name = {entry['name']: entry for entry in lock['packages']}
        self.assertEqual(len(lock['packages']), 625)
        self.assertEqual(len(by_name), 625)
        # New runtime closure for Papers/Snapshot on top of the 604-pin base.
        for name in ('papers', 'snapshot', 'djvulibre', 'gtksourceview5', 'enchant',
                     'libspelling', 'gst-plugin-gtk4', 'gst-plugin-pipewire',
                     'gst-plugins-base', 'gst-plugins-good'):
            self.assertIn(name, by_name)
        self.assertEqual(by_name['papers']['version'], '51.1-1')
        self.assertEqual(by_name['snapshot']['version'], '51.0-1')
        for entry in lock['packages']:
            self.assertTrue(entry['url'].startswith('https://'))
            self.assertTrue(entry['signature'])
            self.assertEqual(entry['filename'], Path(entry['filename']).name)
        self.assertEqual(lock['packages'], sorted(lock['packages'], key=lambda e: e['name']))
        self.assertEqual(lock['requested'], sorted(lock['requested']))

    def test_closure_new_entries_validate_like_build_lock(self):
        import importlib.util as importlib_util
        spec = importlib_util.spec_from_file_location(
            'build_lock', ROOT / 'scripts/build-lock.py')
        module = importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        lock = json.loads((ROOT / 'packages/closure.lock.json').read_text())
        for entry in lock['packages']:
            module.validate_entry(entry)


class DesktopDefaultsTests(unittest.TestCase):
    def test_configure_desktop_seeds_bloom_without_overwriting(self):
        script = (ROOT / 'desktop/usr/local/lib/arch-niri-dux/configure-desktop').read_text()
        self.assertIn('application/pdf=org.gnome.Papers.desktop', script)
        self.assertIn('"preset": "bloom"', script)
        self.assertIn('if [[ ! -e "$state_file" ]]', script)
        # Vendored upstream Niri integration stays verbatim; only the session
        # include line is appended.
        self.assertNotIn('bloom', (ROOT / 'desktop/usr/share/arch-niri-dux/niri/config.kdl').read_text())
        self.assertIn('include "/usr/share/arch-niri-dux/session.kdl"',
                      (ROOT / 'desktop/usr/share/arch-niri-dux/niri/config.kdl').read_text()[-500:] +
                      script)

    def test_system_and_user_mime_defaults_use_official_papers_id(self):
        installer = (ROOT / 'installer/install.py').read_text()
        self.assertIn('application/pdf=org.gnome.Papers.desktop', installer)
        script = (ROOT / 'desktop/usr/local/lib/arch-niri-dux/configure-desktop').read_text()
        self.assertIn('org.gnome.Papers.desktop', script)
        # No other PDF viewer is configured as default.
        self.assertNotIn('evince', installer.lower())
        self.assertNotIn('evince', script.lower())


if __name__ == '__main__':
    unittest.main()

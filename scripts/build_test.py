"""Build artifact unit tests."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import package_lock as module

from verification.digest import digest

import dux_apps as aur


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.entry = dict(
            filename='base-1-any.pkg.tar.zst',
            sha256='a' * 64,
            url='https://example.org/base-1-any.pkg.tar.zst',
            signature='abc',
        )

    def test_invalid_fields_rejected(self):
        invalid = {
            'signature': '',
            'filename': '../base.pkg.tar.zst',
            'url': 'http://example.org/base',
            'sha256': 'invalid',
        }
        for field, value in invalid.items():
            # Each case rebuilds the valid entry so the mutations stay independent.
            with self.subTest(field=field):
                entry = self.entry.copy()
                entry[field] = value
                with self.assertRaises(ValueError):
                    module.validate_entry(entry)

    def test_payload_hash_changes_on_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'payload'
            path.write_bytes(b'original')
            before = digest(path)
            path.write_bytes(b'corrupted')
            self.assertNotEqual(before, digest(path))

    def test_unreadable_cache_falls_back_to_download(self):
        with patch.object(module, 'digest', side_effect=PermissionError):
            self.assertFalse(module.cache_matches(Path('/cache/package'), 'a' * 64))

    def test_lock_refresh_removes_old_package_and_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            old = dest / 'base-0-any.pkg.tar.zst'
            signature = old.with_name(old.name + '.sig')
            current = dest / self.entry['filename']
            for path in (old, signature, current):
                path.touch()
            module.prune_payload(dest, [self.entry])
            self.assertTrue(current.exists())
            self.assertFalse(old.exists())
            self.assertFalse(signature.exists())


class LocalServer:
    """Serve one handler on loopback for the duration of a with block."""

    def __init__(self, handler):
        import http.server

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)

    def __enter__(self):
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()
        return f'http://127.0.0.1:{self.server.server_port}/pkg'

    def __exit__(self, *exc):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def payload_handler(body, requests):
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


class DownloadTests(unittest.TestCase):
    def test_stalled_server_times_out_and_retries_then_cleans_up(self):
        import http.server
        import time

        requests = []

        class StallingHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(1)
                time.sleep(10)

            def log_message(self, *args):
                pass

        with LocalServer(StallingHandler) as url, tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'base-1-any.pkg.tar.zst'
            start = time.monotonic()
            with self.assertRaises(RuntimeError):
                module.download(url, dest, 'a' * 64, timeout=1, attempts=3)
            self.assertLess(time.monotonic() - start, 15)
            self.assertEqual(len(requests), 3)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_successful_download_writes_payload(self):
        body = b'package-payload'
        with LocalServer(payload_handler(body, [])) as url, tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'base-1-any.pkg.tar.zst'
            module.download(url, dest, hashlib.sha256(body).hexdigest(), timeout=5, attempts=2)
            self.assertEqual(dest.read_bytes(), body)

    def test_mismatched_download_never_appears_at_destination(self):
        with LocalServer(payload_handler(b'tampered', [])) as url, tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'base-1-any.pkg.tar.zst'
            with patch.object(module.time, 'sleep'), self.assertRaises(RuntimeError):
                module.download(url, dest, hashlib.sha256(b'expected').hexdigest(), timeout=5, attempts=2)
            self.assertEqual(list(Path(directory).iterdir()), [])


class SharedCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.downloads, self.first, self.second = root / 'downloads', root / 'first', root / 'second'
        for directory in (self.downloads, self.first, self.second):
            directory.mkdir()
        self.body = b'package-payload'

    def entry(self, url):
        return dict(
            name='base',
            filename='base-1-any.pkg.tar.zst',
            sha256=hashlib.sha256(self.body).hexdigest(),
            url=url.replace('http://', 'https://'),
            repository='omarchy',
            signature='abc',
        )

    def fetch(self, entry, dest, url):
        # validate_entry demands HTTPS; the loopback server speaks plain HTTP.
        real_download = module.download
        with patch.object(module, 'download', lambda _, *rest: real_download(url, *rest)):
            module.fetch_package(entry, dest, self.downloads, None)

    def test_second_build_reuses_the_shared_download(self):
        requests = []
        with LocalServer(payload_handler(self.body, requests)) as url:
            entry = self.entry(url)
            self.fetch(entry, self.first, url)
            self.fetch(entry, self.second, url)
        self.assertEqual(len(requests), 1)
        self.assertEqual((self.second / entry['filename']).read_bytes(), self.body)

    def test_corrupt_cache_entry_is_replaced(self):
        requests = []
        with LocalServer(payload_handler(self.body, requests)) as url:
            entry = self.entry(url)
            (self.downloads / entry['filename']).write_bytes(b'corrupt')
            self.fetch(entry, self.first, url)
        self.assertEqual(len(requests), 1)
        self.assertEqual((self.downloads / entry['filename']).read_bytes(), self.body)
        self.assertEqual((self.first / entry['filename']).read_bytes(), self.body)


class RepositoryDatabaseTests(unittest.TestCase):
    def test_reads_desc_fields_from_gzip_and_zstd_databases(self):
        import io
        import tarfile

        # Arch serves gzip databases; Omarchy serves zstd.
        desc = b'%NAME%\nbase\n\n%FILENAME%\nbase-1-any.pkg.tar.zst\n\n%DEPENDS%\nglibc\nbash\n'
        for compression in ('gz', 'zst'):
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / 'repo.db'
                with tarfile.open(database, f'w:{compression}') as archive:
                    member = tarfile.TarInfo('base-1/desc')
                    member.size = len(desc)
                    archive.addfile(member, io.BytesIO(desc))
                [fields] = module.read_database(database)
                self.assertEqual(fields['NAME'], ['base'])
                self.assertEqual(fields['DEPENDS'], ['glibc', 'bash'])


class ReviewedRecipeTests(unittest.TestCase):
    def test_modified_recipe_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'packages/aur/example-bin'
            source.mkdir(parents=True)
            for name in ('PKGBUILD', '.SRCINFO'):
                (source / name).write_text('reviewed contents')
            recipe = dict(name='example-bin', files={name: digest(source / name) for name in ('PKGBUILD', '.SRCINFO')})
            self.assertEqual(aur.validate_recipe(root, recipe), source)
            (source / 'PKGBUILD').write_text('changed contents')
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                aur.validate_recipe(root, recipe)

    def test_recipe_cannot_read_outside_reviewed_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'packages/aur/example-bin'
            source.mkdir(parents=True)
            for name in ('PKGBUILD', '.SRCINFO'):
                (source / name).touch()
            outside = root / 'packages/aur/outside'
            outside.write_text('outside')
            recipe = dict(name='example-bin', files={name: digest(source / name) for name in ('PKGBUILD', '.SRCINFO')})
            recipe['files']['../outside'] = digest(outside)
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                aur.validate_recipe(root, recipe)


class LiveKeyringTests(unittest.TestCase):
    def test_key_operations_wait_for_live_initializer_and_stop_if_it_fails(self):
        import os
        import subprocess

        source = Path(__file__).with_name('build-guest').read_text()
        preparation = source.split('# Serialize keyring preparation', 1)[1].split('# Import the existing', 1)[0]
        preparation = '# Serialize keyring preparation' + preparation
        for status in (0, 9):
            with self.subTest(initializer_status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                systemctl = root / 'systemctl'
                systemctl.write_text(
                    '#!/bin/bash\nsleep 0.02\n'
                    f'if [ {status} != 0 ]; then exit {status}; fi\n'
                    'touch "$KEYRING_TEST_STATE/ready"\n'
                )
                pacman_key = root / 'pacman-key'
                pacman_key.write_text(
                    '#!/bin/bash\n'
                    'test -f "$KEYRING_TEST_STATE/ready" || exit 29\n'
                    'printf "%s\\n" "$*" >> "$KEYRING_TEST_STATE/operations"\n'
                )
                systemctl.chmod(0o755)
                pacman_key.chmod(0o755)
                environment = os.environ | {
                    'PATH': str(root) + ':' + os.environ['PATH'],
                    'KEYRING_TEST_STATE': str(root),
                }
                result = subprocess.run(['bash', '-e', '-c', preparation], env=environment, check=False)
                self.assertEqual(result.returncode, status)
                operations = root / 'operations'
                if status == 0:
                    self.assertEqual(operations.read_text().splitlines(), ['--init', '--populate archlinux'])
                else:
                    self.assertFalse(operations.exists())


class ResolveDateTests(unittest.TestCase):
    def test_missing_snapshot_falls_back_to_previous_day(self):
        import datetime
        import urllib.error
        from unittest.mock import MagicMock

        available = MagicMock()
        available.__enter__.return_value = available
        missing = urllib.error.HTTPError('https://example.invalid/core.db', 404, 'Not Found', None, None)
        with patch('urllib.request.urlopen', side_effect=[missing, available]) as opened:
            self.assertEqual(
                module.resolve_date(None), (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y/%m/%d')
            )
        self.assertEqual(opened.call_count, 2)

    def test_server_error_aborts_instead_of_drifting_to_an_older_date(self):
        import urllib.error

        failing = urllib.error.HTTPError('https://example.invalid/core.db', 500, 'Server Error', None, None)
        with patch('urllib.request.urlopen', side_effect=failing):
            with self.assertRaises(urllib.error.HTTPError):
                module.resolve_date(None)

    def test_explicit_date_is_used_verbatim(self):
        from unittest.mock import MagicMock

        available = MagicMock()
        available.__enter__.return_value = available
        with patch('urllib.request.urlopen', return_value=available) as opened:
            self.assertEqual(module.resolve_date('2026/11/10'), '2026/11/10')
        self.assertEqual(opened.call_count, 1)


class OmarchyDepsTests(unittest.TestCase):
    def test_pin_dependencies_exclude_the_pinned_packages(self):
        pin = [
            dict(name='limine-mkinitcpio-hook', depends=['bash', 'limine']),
            dict(name='limine-snapper-sync', depends=['limine-mkinitcpio-hook', 'libnotify']),
        ]
        self.assertEqual(module.omarchy_arch_deps(pin), {'bash', 'limine', 'libnotify'})


class VendorDesktopTests(unittest.TestCase):
    def tree(self, directory, pkgbuild):
        root = Path(directory)
        (root / 'desktop/usr/share/arch-niri-dux/niri/dms').mkdir(parents=True)
        (root / 'packages/integration').mkdir(parents=True)
        (root / 'packages/integration/PKGBUILD').write_text(pkgbuild)
        return root

    def test_refresh_rewrites_files_and_dux_shell_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(directory, "depends=('dux-shell>=2.1.0')\n")
            module.vendor_desktop(root, 'v2.1.2', [f'body-{i}\n'.encode() for i in range(6)])
            for _, path in module.VENDORED:
                self.assertTrue((root / path).is_file())
            self.assertIn("'dux-shell>=2.1.2'", (root / 'packages/integration/PKGBUILD').read_text())

    def test_rerun_with_the_same_tag_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(directory, "depends=('dux-shell>=2.1.0')\n")
            module.vendor_desktop(root, 'v2.1.2', [b'body\n'] * 6)
            module.vendor_desktop(root, 'v2.1.2', [b'body\n'] * 6)
            self.assertIn("'dux-shell>=2.1.2'", (root / 'packages/integration/PKGBUILD').read_text())

    def test_missing_dux_shell_floor_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(directory, "depends=('niri')\n")
            with self.assertRaisesRegex(ValueError, 'no dux-shell floor'):
                module.vendor_desktop(root, 'v2.1.2', [b'body\n'] * 6)

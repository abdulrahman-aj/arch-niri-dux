"""Build artifact unit tests."""

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


from tools.packages import inputs as module
from tools.digest import digest

from tools.packages import dux_apps


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
        with tempfile.TemporaryDirectory() as directory:
            # digest() on a directory raises OSError; the cache stays optional.
            self.assertFalse(module.cache_matches(Path(directory), 'a' * 64))

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
        # Redirect at the stdlib boundary so the real download runs unpatched.
        import urllib.request

        real_urlopen = urllib.request.urlopen

        def redirect(target, *args, **kwargs):
            if isinstance(target, str) and target.startswith('https://127.0.0.1:'):
                target = 'http://' + target[len('https://') :]
            return real_urlopen(target, *args, **kwargs)

        with patch.object(module.urllib.request, 'urlopen', side_effect=redirect):
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
            source = root / 'packages/dux-apps/example-bin'
            source.mkdir(parents=True)
            for name in ('PKGBUILD', '.SRCINFO'):
                (source / name).write_text('reviewed contents')
            recipe = dict(name='example-bin', files={name: digest(source / name) for name in ('PKGBUILD', '.SRCINFO')})
            self.assertEqual(dux_apps.validate_recipe(root, recipe), source)
            (source / 'PKGBUILD').write_text('changed contents')
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                dux_apps.validate_recipe(root, recipe)

    def test_recipe_cannot_read_outside_reviewed_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'packages/dux-apps/example-bin'
            source.mkdir(parents=True)
            for name in ('PKGBUILD', '.SRCINFO'):
                (source / name).touch()
            outside = root / 'packages/dux-apps/outside'
            outside.write_text('outside')
            recipe = dict(name='example-bin', files={name: digest(source / name) for name in ('PKGBUILD', '.SRCINFO')})
            recipe['files']['../outside'] = digest(outside)
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                dux_apps.validate_recipe(root, recipe)


class ResolveDateTests(unittest.TestCase):
    def test_missing_snapshot_falls_back_to_previous_day(self):
        import datetime
        import urllib.error
        from unittest.mock import MagicMock

        available = MagicMock()
        available.__enter__.return_value = available
        missing = urllib.error.HTTPError('https://example.invalid/core.db', 404, 'Not Found', None, None)
        attempts = []

        def fake_urlopen(*args, **_kwargs):
            attempts.append(args)
            if len(attempts) == 1:
                raise missing
            return available

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            self.assertEqual(
                module.resolve_date(None), (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y/%m/%d')
            )
        self.assertEqual(len(attempts), 2)

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
        with patch('urllib.request.urlopen', return_value=available):
            self.assertEqual(module.resolve_date('2026/11/10'), '2026/11/10')


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


class BootstrapMirrorTests(unittest.TestCase):
    """build-fetch-bootstrap tries the Omarchy mirror before geo.mirror.pkgbuild.com."""

    def test_omarchy_is_tried_first_with_geo_fallback(self):
        import os
        import subprocess

        body = b'bootstrap-image'
        sha = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            requests_log = root / 'requests.txt'
            requests_log.write_text('')
            (fake_bin / 'curl').write_text(
                '#!/bin/bash\n'
                'url=; out=; prev=\n'
                'for arg in "$@"; do\n'
                '    if [[ $prev == -o ]]; then out=$arg; fi\n'
                '    if [[ $arg == http* ]]; then url=$arg; fi\n'
                '    prev=$arg\n'
                'done\n'
                'printf "%s\\n" "$url" >> "$REQUESTS_LOG"\n'
                'if [[ $url == *mirror.omarchy.org* ]]; then exit 22; fi\n'
                'printf bootstrap-image > "$out"\n'
            )
            (fake_bin / 'curl').chmod(0o755)
            (fake_bin / 'gpgv').write_text('#!/bin/bash\ntest -f "$3"\n')
            (fake_bin / 'gpgv').chmod(0o755)
            # Only these host utilities stay reachable; every other name on
            # PATH is a fake, so a missing curl fake fails closed instead of
            # downloading from the real network.
            for name in ('bash', 'basename', 'dirname', 'mkdir', 'mktemp', 'mv', 'rm', 'sha256sum'):
                target = fake_bin / name
                if not target.exists():
                    target.symlink_to(shutil.which(name))
            cache, partial = root / 'cache', root / 'partial'
            cache.mkdir()
            partial.mkdir()
            script = Path(__file__).resolve().parents[1] / 'tools/build/fetch_bootstrap.sh'
            environment = os.environ | {
                'PATH': str(fake_bin),
                'REQUESTS_LOG': str(requests_log),
            }
            result = subprocess.run(
                ['bash', str(script), '2026.10.01', sha, str(cache), str(partial)],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            requested = requests_log.read_text().splitlines()
            self.assertGreaterEqual(len(requested), 2, requested)
            self.assertTrue(requested[0].startswith('https://mirror.omarchy.org/iso/'), requested)
            self.assertTrue(requested[1].startswith('https://geo.mirror.pkgbuild.com/iso/'), requested)
            self.assertEqual((cache / 'archlinux-2026.10.01-x86_64.iso').read_bytes(), body)

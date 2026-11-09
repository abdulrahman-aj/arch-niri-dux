"""Release button checker shows only on newer arch-niri-dux releases."""

import importlib.util
import io
import json
import struct
import subprocess
import tarfile
import tempfile
import types
import typing
import unittest
import zlib
from pathlib import Path

from scripts.verification import dux_update


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'desktop/usr/lib/arch-niri-dux/release-update-check'
import importlib.machinery

loader = importlib.machinery.SourceFileLoader('release_update_check', str(CHECKER))
spec = importlib.util.spec_from_loader('release_update_check', loader)
checker = importlib.util.module_from_spec(spec)
loader.exec_module(checker)

SETTINGS_SPEC = importlib.util.spec_from_file_location(
    'dux_settings', ROOT / 'desktop/usr/lib/arch-niri-dux/initialize-dux-settings.py'
)
settings = importlib.util.module_from_spec(SETTINGS_SPEC)
SETTINGS_SPEC.loader.exec_module(settings)


def make_db(path, entries):
    """Write a repository database tar with (name, version) desc entries."""
    with tarfile.open(path, 'w:gz') as archive:
        for name, version in entries:
            directory = f'{name}-{version}' if version else name
            desc = f'%NAME%\n{name}\n\n%VERSION%\n{version}\n' if version else f'%NAME%\n{name}\n'
            data = desc.encode()
            info = tarfile.TarInfo(f'{directory}/desc')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


class CheckerDecisionTest(unittest.TestCase):
    def test_newer_release_shows(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux', '0.3.2-1')])
            self.assertEqual(checker.candidate_version_from_db(db), '0.3.2-1')
            self.assertTrue(checker.is_newer('0.3.1-1', '0.3.2-1'))
            self.assertTrue(checker.check(db_urls=[f'file://{db}'], installed='0.3.1-1'))

    def test_same_version_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux', '0.3.1-1')])
            self.assertFalse(checker.is_newer('0.3.1-1', '0.3.1-1'))
            self.assertFalse(checker.check(db_urls=[f'file://{db}'], installed='0.3.1-1'))

    def test_older_candidate_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux', '0.3.1-1')])
            self.assertFalse(checker.is_newer('0.3.2-1', '0.3.1-1'))
            self.assertFalse(checker.check(db_urls=[f'file://{db}'], installed='0.3.2-1'))

    def test_offline_hides_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.db'
            result = subprocess.run(
                ['python3', str(CHECKER)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={
                    'PATH': '/usr/bin:/bin',
                    'ARCH_NIRI_DUX_DB_URL': f'file://{missing}',
                    'ARCH_NIRI_DUX_INSTALLED_VERSION': '0.3.1-1',
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')

    def test_malformed_database_hides(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / 'broken.db'
            broken.write_bytes(b'not a database')
            self.assertIsNone(checker.candidate_version_from_db(broken))
            self.assertFalse(checker.check(db_urls=[f'file://{broken}'], installed='0.3.1-1'))
            empty = Path(directory) / 'empty.db'
            make_db(empty, [('arch-niri-dux-baseline', '0.3.2-1')])
            self.assertIsNone(checker.candidate_version_from_db(empty))
            self.assertFalse(checker.check(db_urls=[f'file://{empty}'], installed='0.3.1-1'))

    def test_ignores_baseline_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'repo.db'
            make_db(db, [('arch-niri-dux-baseline', '9.9.9-1'), ('arch-niri-dux', '0.3.1-1')])
            self.assertEqual(checker.candidate_version_from_db(db), '0.3.1-1')


class PluginSeedTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name)

    def test_fresh_account_enables_plugin(self):
        settings.initialize(self.config, {})
        value = json.loads((self.config / 'dms/plugin_settings.json').read_text())
        self.assertEqual(value, {settings.PLUGIN_ID: {'enabled': True}})

    def test_explicit_opt_out_survives(self):
        target = self.config / 'dms/plugin_settings.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({settings.PLUGIN_ID: {'enabled': False}}))
        settings.initialize(self.config, {})
        value = json.loads(target.read_text())
        self.assertEqual(value, {settings.PLUGIN_ID: {'enabled': False}})

    def test_other_plugins_preserved(self):
        target = self.config / 'dms/plugin_settings.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'otherPlugin': {'enabled': True}}))
        settings.initialize(self.config, {})
        value = json.loads(target.read_text())
        self.assertEqual(value, {'otherPlugin': {'enabled': True}, settings.PLUGIN_ID: {'enabled': True}})

    def test_bar_seed_uses_plugin_widget(self):
        settings.initialize(self.config)
        merged = {}
        for path in sorted((self.config / 'dms/config.d').glob('*.json')):
            merged.update(json.loads(path.read_text()))
        bars = merged['barConfigs']
        center = next(bar for bar in bars if bar['id'] == 'default')['centerWidgets']
        ids = [entry if isinstance(entry, str) else entry.get('id') for entry in center]
        self.assertIn(settings.PLUGIN_ID, ids)
        self.assertNotIn('systemUpdate', ids)
        self.assertEqual(merged['updaterIntervalSeconds'], 7 * 24 * 60 * 60)


BAR_BACKGROUND = (19, 17, 23)
PILL = (33, 31, 36)
BAR_WIDTH = 1280
BAR_HEIGHT = 44


def bar_png(path, pills, width=BAR_WIDTH, height=BAR_HEIGHT):
    """Render a top-bar strip PNG whose middle row carries the given pill runs."""
    rows = [[BAR_BACKGROUND for _ in range(width)] for _ in range(height)]
    for start, end in pills:
        for x in range(start, end):
            for y in range(8, height - 8):
                rows[y][x] = PILL

    def chunk(tag, payload):
        return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', zlib.crc32(tag + payload))

    raw = b''.join(b'\x00' + bytes(v for pixel in row for v in pixel) for row in rows)
    path.write_bytes(
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw))
        + chunk(b'IEND', b'')
    )


def locator_click(none_pills, available_pills):
    """Render the pair and run update_button; return its click point in pixels."""
    with tempfile.TemporaryDirectory() as directory:
        vm = types.SimpleNamespace(directory=Path(directory))
        bar_png(Path(directory) / dux_update.NONE, none_pills)
        bar_png(Path(directory) / dux_update.AVAILABLE, available_pills)
        x, y = dux_update.update_button(vm)
    return x * BAR_WIDTH, y * BAR_HEIGHT


class UpdateButtonLocatorTest(unittest.TestCase):
    """The bar-pill locator must click the pill the offer added, nowhere else."""

    # Bar geometries transcribed from the archived verification screenshots
    # (out/verify/*/dux-update-{none,available}.png). The clock rollover pair is
    # the failed real run: the clock resized during the 5-minute wait, so the
    # old width-based locator found no candidate before any click. The labelled
    # pair is an earlier bar where an Update pill reused an old widget slot and
    # pushed the weather pill right with its width intact.
    CLOCK_ROLLOVER_PAIR = (
        (
            (0, 4),
            (8, 36),
            (40, 83),
            (513, 638),
            (642, 696),
            (948, 982),
            (986, 1016),
            (1020, 1086),
            (1090, 1148),
            (1152, 1182),
            (1186, 1216),
            (1220, 1272),
            (1276, 1280),
        ),
        (
            (0, 4),
            (8, 36),
            (40, 83),
            (520, 638),
            (642, 672),
            (676, 730),
            (948, 982),
            (986, 1016),
            (1020, 1086),
            (1090, 1148),
            (1152, 1182),
            (1186, 1216),
            (1220, 1272),
            (1276, 1280),
        ),
    )
    UNCHANGED_CLOCK_PAIR = (
        (
            (0, 4),
            (8, 36),
            (40, 83),
            (525, 638),
            (642, 696),
            (948, 982),
            (986, 1016),
            (1020, 1086),
            (1090, 1148),
            (1152, 1182),
            (1186, 1216),
            (1220, 1272),
            (1276, 1280),
        ),
        (
            (0, 4),
            (8, 36),
            (40, 83),
            (525, 638),
            (642, 672),
            (676, 730),
            (948, 982),
            (986, 1016),
            (1020, 1086),
            (1090, 1148),
            (1152, 1182),
            (1186, 1216),
            (1220, 1272),
            (1276, 1280),
        ),
    )
    LABELLED_BUTTON_PAIR = (
        (
            (0, 4),
            (8, 36),
            (40, 83),
            (525, 638),
            (642, 696),
            (948, 982),
            (986, 1016),
            (1020, 1086),
            (1090, 1148),
            (1152, 1182),
            (1186, 1216),
            (1220, 1272),
            (1276, 1280),
        ),
        (
            (0, 4),
            (8, 36),
            (40, 83),
            (525, 638),
            (642, 716),
            (720, 774),
            (948, 982),
            (986, 1016),
            (1020, 1086),
            (1090, 1148),
            (1152, 1182),
            (1186, 1216),
            (1220, 1272),
            (1276, 1280),
        ),
    )
    # Archive directory -> span the click must land in, as inspected on the
    # screenshots; None marks an archive whose pair shows no release, where the
    # locator must refuse. Archive names outside this map have no manually
    # confirmed truth and stay out.
    ARCHIVED_TRUTH: typing.ClassVar[dict[str, tuple[int, int] | None]] = {
        'dux-verify-3u29xr_b': (642, 672),
        'dux-verify-95g93igi': (642, 672),
        'dux-verify-9dyensxm': (642, 672),
        'dux-verify-bkok0_xt': (642, 716),
        'dux-verify-dbxrtugc': (642, 672),
        'dux-verify-duad_oih': (642, 672),
        'dux-verify-jhrbb2ul': (642, 672),
        'dux-verify-p9bcufcw': (642, 672),
        'dux-verify-x5rg2_vq': (642, 716),
        'dux-verify-_snzcmqy': None,
    }

    def assert_pair_renders_exactly(self, pair):
        none_pills, available_pills = pair
        with tempfile.TemporaryDirectory() as directory:
            none = Path(directory) / dux_update.NONE
            available = Path(directory) / dux_update.AVAILABLE
            bar_png(none, none_pills)
            bar_png(available, available_pills)
            self.assertEqual(dux_update.bar_pills(none), list(none_pills))
            self.assertEqual(dux_update.bar_pills(available), list(available_pills))

    def test_rendered_bars_reproduce_the_recorded_pills(self):
        for pair in (self.CLOCK_ROLLOVER_PAIR, self.UNCHANGED_CLOCK_PAIR, self.LABELLED_BUTTON_PAIR):
            self.assert_pair_renders_exactly(pair)

    def test_recorded_clock_rollover_clicks_the_added_pill(self):
        self.assert_click_in(*self.CLOCK_ROLLOVER_PAIR, (642, 672))

    def test_recorded_unchanged_clock_clicks_the_added_pill(self):
        self.assert_click_in(*self.UNCHANGED_CLOCK_PAIR, (642, 672))

    def test_recorded_labelled_button_clicks_the_added_pill(self):
        self.assert_click_in(*self.LABELLED_BUTTON_PAIR, (642, 716))

    def test_insertion_is_found_at_other_widget_positions(self):
        # A +2 px offset at the displaced left edge is the tolerance boundary.
        none_pills = ((10, 40), (60, 89), (120, 300))
        available_pills = ((10, 40), (60, 89), (122, 134), (138, 300))
        self.assert_click_in(none_pills, available_pills, (120, 140))

    def test_insertion_off_the_displaced_left_edge_is_refused(self):
        none_pills = ((10, 40), (60, 89), (120, 300))
        available_pills = ((10, 40), (60, 89), (123, 134), (138, 300))
        with self.assertRaises(AssertionError):
            locator_click(none_pills, available_pills)

    def test_identical_screenshots_stay_refused(self):
        none_pills = ((0, 4), (8, 36), (40, 83), (525, 638), (642, 696))
        with self.assertRaises(AssertionError):
            locator_click(none_pills, none_pills)

    def test_conflicting_insertions_are_refused(self):
        none_pills = ((10, 40), (50, 89), (120, 300))
        available_pills = ((10, 40), (50, 64), (68, 89), (120, 134), (138, 300))
        with self.assertRaises(AssertionError):
            locator_click(none_pills, available_pills)

    def test_archived_evidence_replays_to_the_true_pill(self):
        # The archive only exists on checkouts that ran verifications; each
        # confirmed value above was read off that directory's screenshots.
        archive_pair = ROOT / 'out' / 'verify'
        if not archive_pair.is_dir():
            self.skipTest('no archived verification evidence on this checkout')
        for archive in sorted(archive_pair.iterdir()):
            none = archive / 'dux-update-none.png'
            available = archive / 'dux-update-available.png'
            if not none.is_file() or not available.is_file():
                continue
            with self.subTest(archive=archive.name):
                if archive.name not in self.ARCHIVED_TRUTH:
                    continue
                expected = self.ARCHIVED_TRUTH[archive.name]
                pills_before, pills_after = dux_update.bar_pills(none), dux_update.bar_pills(available)
                if expected is None:
                    with self.assertRaises(AssertionError):
                        dux_update.added_pill(pills_before, pills_after)
                    continue
                span = dux_update.added_pill(pills_before, pills_after)
                self.assertEqual(span, expected)

    def assert_click_in(self, none_pills, available_pills, span):
        click_x, click_y = locator_click(none_pills, available_pills)
        self.assertGreaterEqual(click_x, span[0])
        self.assertLess(click_x, span[1])
        self.assertEqual(click_y, dux_update.BAR_MIDDLE)


if __name__ == '__main__':
    unittest.main()

"""The bar-pill locator clicks the pill the release offer added, nowhere else."""

import struct
import tempfile
import types
import unittest
import zlib
from pathlib import Path

from scripts.verification import dux_update


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
    """Bar geometries transcribed from archived verification screenshots
    (dux-update-{none,available}.png). The clock rollover pair is the failed
    real run: the clock resized during the 5-minute wait, so the old
    width-based locator found no candidate before any click. The labelled pair
    is an earlier bar where an Update pill reused an old widget slot and pushed
    the weather pill right with its width intact.
    """

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

    def assert_click_in(self, none_pills, available_pills, span):
        click_x, click_y = locator_click(none_pills, available_pills)
        self.assertGreaterEqual(click_x, span[0])
        self.assertLess(click_x, span[1])
        self.assertEqual(click_y, dux_update.BAR_MIDDLE)


if __name__ == '__main__':
    unittest.main()

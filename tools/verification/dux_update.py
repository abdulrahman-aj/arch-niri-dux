"""Dux announces pending updates on its own schedule; its updater installs them.

The packaged defaults put our release button on the bar, hidden while no
newer arch-niri-dux release is available, and make Dux's server check every 6
hours, with no check at login. Dux schedules from its last check, persisted in
~/.cache/dms, so logins and reboots don't reset the interval. This stage
shortens the interval to Dux's 5-minute minimum the way a user would, lets the
scheduled check find a fixture repository with nothing newer, re-logs in
before the interval to show no extra check runs, then offers our packages one
pkgrel newer and lets the next scheduled check find them. The pill appears when
Dux's own check finds the newer arch-niri-dux, with no rescan or visibility
workaround. The host clicks the button, sends Update All's request, types the
password into Dux's real authentication dialog, and checks the installed
release end to end. Dux runs `pkexec pacman -Syu --needed --noconfirm`
through its pacman backend because no AUR helper is installed.
"""

import subprocess
import time
from tools.verification.images import png_dimensions, png_top_rows
from tools.verification.constants import PASSWORD, AVAILABLE, BAR_ABOVE_PILLS, BAR_MIDDLE, NONE, PANEL, PROMPT
from tools.verification.payload import probe


def screen_shows(path, *phrases):
    text = subprocess.check_output(
        ['tesseract', str(path), 'stdout', '--psm', '11'], text=True, stderr=subprocess.DEVNULL
    ).lower()
    return all(phrase in text for phrase in phrases)


def bar_pills(path):
    """Return (start, end) columns of the widget pills on the top bar."""
    rows = png_top_rows(path, BAR_MIDDLE + 1)
    row = rows[BAR_MIDDLE]
    # Pills are inset from the bar's top edge, so that strip is bar background.
    background = max(set(rows[BAR_ABOVE_PILLS]), key=rows[BAR_ABOVE_PILLS].count)
    pills, start = [], None
    for x, pixel in enumerate(row + [background]):
        if pixel != background and start is None:
            start = x
        elif pixel == background and start is not None:
            pills.append((start, x))
            start = None
    return pills


def added_pill(before, after):
    """Return the (start, end) span of the one pill the after screenshot gained.

    Dux's bar lays out pills with a fixed gap, so inserting a widget makes the
    new pill start at the same column as the pill it displaces, and every
    later pill shifts right with its width intact. The clock can still resize
    on a minute or date rollover, so widths are not the invariant; displaced
    left edges are. Ambiguity raises instead of guessing a click.
    """
    assert len(after) == len(before) + 1, ('Expected exactly one new bar pill', before, after)
    lefts = [start for start, _ in before]
    added = [
        (start, end)
        for start, end in after
        if (start, end) not in before and any(abs(start - left) <= 2 for left in lefts)
    ]
    assert len(added) == 1, ('Release pill is not uniquely identified', before, after, added)
    return added[0]


def update_button(vm):
    """Locate the release pill that appeared once a newer release was offered."""
    start, end = added_pill(bar_pills(vm.directory / NONE), bar_pills(vm.directory / AVAILABLE))
    width, height = png_dimensions(vm.directory / AVAILABLE)
    return (start + end) / 2 / width, BAR_MIDDLE / height


def verify(vm, passed):
    probe(vm, 'dux_update', 'prepare', timeout=600)
    probe(vm, 'dux_update', 'offer', timeout=300)
    passed(
        "Dux's scheduled checks found no release with the button hidden, skipped a re-login, then showed it for ours"
    )
    vm.click(*update_button(vm))
    # The button opens Dux's updater panel. OCR cannot read its buttons, so the
    # upgrade is the same server request its Update All sends.
    deadline = time.monotonic() + 30
    while True:
        probe(vm, 'dux_update', 'panel-screenshot')
        if screen_shows(vm.directory / PANEL, 'updates', 'arch-niri-dux'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"Dux's updater panel did not list our package; see {PANEL}.")
        time.sleep(1)
    vm.key('esc')
    probe(vm, 'dux_update', 'upgrade', timeout=120)
    # Dux's polkit agent opens its authentication dialog for pkexec.
    deadline = time.monotonic() + 60
    while True:
        probe(vm, 'dux_update', 'prompt-screenshot')
        if screen_shows(vm.directory / PROMPT, 'authentication'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f'Dux authentication dialog did not appear; see {PROMPT}.')
        time.sleep(2)
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    probe(vm, 'dux_update', 'check', timeout=420)
    passed("The bar button opened Dux's updater, which installed the offered release")

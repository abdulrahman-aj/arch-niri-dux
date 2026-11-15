"""Desktop acceptance behavior."""

import time
from tools.verification.constants import PASSWORD
from tools.verification.login import click_screen_text
from tools.verification.payload import probe


def verify(vm, passed):
    probe(vm, 'desktop', 'desktop', timeout=180)
    passed('Niri, Dux, terminal, Neovim and Chromium smoke checks')
    probe(vm, 'desktop', 'session-path', timeout=90)
    passed('Login shells, user services and Niri spawns resolve local user executables')
    probe(vm, 'desktop', 'dux-components', timeout=300)
    passed('GOP metrics, live file indexing, Calendar event create/read and Dux Files integration')
    probe(vm, 'desktop', 'file-manager-activation', timeout=180)
    passed('Cold FileManager1 activation reaches Dux Files with a clean journal')
    probe(vm, 'desktop', 'themes', timeout=300)
    passed('Live Dux light/dark changes reach Chromium, GTK settings, portals and Dux apps')
    # The native first-login welcome must not cover the document/camera UI.
    probe(vm, 'desktop', 'onboarding-screen')
    if click_screen_text(vm, vm.directory / '02-onboarding.png', 'Skip'):
        time.sleep(0.5)
    probe(vm, 'desktop', 'wallpaper', timeout=90)
    passed('Bloom material wallpaper is the fresh-user default and rendered')
    probe(vm, 'desktop', 'account-seeded', timeout=180)
    passed('New account receives the seeded desktop defaults')


def verify_lock(vm, passed):
    probe(vm, 'desktop', 'lock')
    probe(vm, 'desktop', 'locked')
    vm.screenshot('03-locked')
    vm.type_graphical(PASSWORD)
    vm.chord('ret')
    vm.command(
        'for attempt in $(seq 1 30); do cd /run/verify && python -m tools.verification.probes.desktop unlocked && exit 0; sleep 1; done; exit 1',
        timeout=60,
    )
    passed('Screen lock and password unlock')

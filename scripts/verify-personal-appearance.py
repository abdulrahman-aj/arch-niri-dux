#!/usr/bin/env python3
"""Guest-only Zen and T3 appearance checks after the real personal bootstrap."""

import importlib.util
import json
from pathlib import Path
import subprocess


DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('verify_guest', DIRECTORY / 'verify-guest.py')
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)

ZEN_PAGE = '''<!doctype html>
<meta charset="utf-8"><meta name="color-scheme" content="light dark">
<style>body { color-scheme: light dark; font: 24px sans-serif; padding: 40px; }</style>
<h1>Zen desktop appearance verification</h1><p id="state"></p>
<script>
const media = matchMedia('(prefers-color-scheme: dark)');
let changes = 0;
function publish() {
  const state = {scheme: media.matches ? 'dark' : 'light', changes};
  document.title = 'DUX-ZEN ' + JSON.stringify(state);
  document.getElementById('state').textContent = JSON.stringify(state);
}
media.addEventListener('change', () => { changes++; publish(); });
publish();
</script>
'''


def zen_state():
    for window in guest.windows():
        title = window.get('title') or ''
        marker = title.find('DUX-ZEN ')
        if marker >= 0:
            # Zen appends its browser name after the page title.
            return json.JSONDecoder().raw_decode(title[marker + len('DUX-ZEN '):])[0]
    return None


def t3_state():
    return json.loads(guest.user_command(
        'node', str(DIRECTORY / 'verify-personal-cdp.mjs'), '9223', capture=True).stdout)


def start(unit, *command):
    guest.user_command('systemd-run', '--user', '--quiet', '--collect',
                       '--unit=' + unit, '--property=TimeoutStopSec=10', *command)


def main():
    page = Path('/tmp/zen-appearance.html')
    page.write_text(ZEN_PAGE)
    guest.user_command('mkdir', '-p', '/tmp/zen-appearance-profile', '/tmp/t3-appearance-profile')
    evidence = []
    try:
        # These flags isolate test profiles and expose inspection, not appearance.
        start('verify-zen-appearance', 'zen-browser', '--no-remote', '--profile',
              '/tmp/zen-appearance-profile', '--no-default-browser-check', page.as_uri())
        start('verify-t3-appearance', 't3code', '--user-data-dir=/tmp/t3-appearance-profile',
              '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=9223')
        guest.wait_until('Zen page', zen_state, timeout=90)
        guest.wait_until('T3 renderer', t3_state, timeout=90)
        for mode in ('dark', 'light', 'dark'):
            guest.user_command('dms', 'ipc', 'call', 'theme', mode)

            def follows_desktop():
                zen, t3 = zen_state(), t3_state()
                observed = {'dux': mode, 'zen': zen, 't3': t3}
                Path('/run/verify/personal-appearance-last.json').write_text(
                    json.dumps(observed, indent=2) + '\n')
                if (zen and zen['scheme'] == mode and t3['scheme'] == mode
                        and t3['darkClass'] == (mode == 'dark')):
                    return observed
                return None

            state = guest.wait_until(f'Zen and T3 following Dux {mode}', follows_desktop)
            evidence.append(state)
        assert evidence[-1]['zen']['changes'] >= 2, evidence
        assert evidence[-1]['t3']['changes'] >= 2, evidence
        guest.screenshot('personal-appearance.png')
        Path('/run/verify/personal-appearance.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('Zen and T3 followed live Dux dark/light/dark transitions.')
    finally:
        for unit in ('verify-zen-appearance', 'verify-t3-appearance'):
            try:
                guest.user_command('systemctl', '--user', 'stop', unit)
            except subprocess.CalledProcessError:
                pass


if __name__ == '__main__':
    main()

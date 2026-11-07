"""Updates started from Dux's updater upgrade our package and run migrations.

Dux 2.1.0's GUI entry points (Settings > Software Updates, the bar popout)
all call SystemUpdateService.runUpdates, which sends `sysupdate.upgrade` to
the session's DMS server. With no AUR helper installed the server selects its
pacman backend and runs `pkexec pacman -Syu --needed --noconfirm`, answered by
Dux's own polkit agent. This stage sends the same server requests, types the
password into Dux's real authentication dialog, and checks the migration
contract end to end. Only the QML button press is skipped.
"""

import json
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import time

from . import dispatch, migrations
from .apps import daemon_request
from .guest import run, screenshot, user_command, wait_until
from .vm import PASSWORD

EVIDENCE = Path('/run/verify')
# Probe steps run as separate processes around host keyboard input.
WORK = Path('/var/tmp/dux-update-fixture')
PACMAN_CONF = Path('/etc/pacman.conf')
SAVED_CONF = WORK / 'pacman.conf.original'
REPOSITORY = 'dux-update-fixture'
SYSTEM_EFFECT = Path('/var/lib/arch-niri-dux/dux-update-effects')
USER_EFFECT = Path('/home/tester/.local/state/dux-update-effects')
FIXTURE_NUMBER = 30
PACKAGES = ('arch-niri-dux', 'arch-niri-dux-baseline')
PROMPT = 'dux-update-prompt.png'


def installed():
    return subprocess.check_output(['pacman', '-Q', *PACKAGES], text=True).strip()


def build_candidate():
    """Build our packages one pkgrel step newer, carrying fixture migrations."""
    user = pwd.getpwnam('tester')
    source = WORK / 'source'
    source.mkdir()
    run('tar', '-xf', str(EVIDENCE / 'integration-input/integration.tar'), '-C', str(source))
    recipe = (EVIDENCE / 'integration-input/PKGBUILD').read_text()
    # N.1 sorts between this release and the integration upgrade's N+1 and N+2
    # candidates, so that later stage still upgrades from what we install.
    recipe, changes = re.subn(r'^pkgrel=(\d+)$', r'pkgrel=\1.1', recipe, flags=re.MULTILINE)
    assert changes == 1
    recipe_path = source / 'packages/integration/PKGBUILD'
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text(recipe)
    fixtures = source / 'desktop/usr/lib/arch-niri-dux/migrations'
    migrations.fixture(fixtures, 'system', FIXTURE_NUMBER, migrations.effect(SYSTEM_EFFECT, 'dux-updater'))
    migrations.fixture(
        fixtures, 'user', FIXTURE_NUMBER, migrations.effect('"$HOME/.local/state/dux-update-effects"', 'dux-updater')
    )
    run('chown', '-R', f'{user.pw_uid}:{user.pw_gid}', str(WORK))
    packages = WORK / 'packages'
    run(
        'runuser',
        '-u',
        user.pw_name,
        '--',
        'env',
        '-u',
        'XDG_CONFIG_HOME',
        '-u',
        'XDG_STATE_HOME',
        '-u',
        'XDG_DATA_HOME',
        f'HOME={user.pw_dir}',
        'python',
        str(EVIDENCE / 'integration_build.py'),
        str(source),
        str(WORK / 'build'),
        str(packages),
        '--epoch',
        '1234',
    )
    run('repo-add', str(packages / f'{REPOSITORY}.db.tar.gz'), *map(str, packages.glob('*.pkg.tar.zst')))
    return packages


def use_fixture_repository(packages):
    """Point the system pacman configuration at the fixture repository only.

    Dux checks and upgrades through /etc/pacman.conf, and the guest is offline,
    so the online repositories it includes are swapped out until cleanup.
    Signature enforcement is qualified by the integration upgrade stage.
    """
    original = PACMAN_CONF.read_text()
    include = 'Include = /etc/pacman.d/arch-niri-dux.conf'
    assert original.count(include) == 1, original
    SAVED_CONF.write_text(original)
    PACMAN_CONF.write_text(
        original.replace(include, f'[{REPOSITORY}]\nSigLevel = Optional TrustAll\nServer = file://{packages}')
    )


def restore_pacman_conf():
    if SAVED_CONF.exists():
        PACMAN_CONF.write_text(SAVED_CONF.read_text())


def prepare():
    run('systemd-detect-virt', '--vm')
    assert (EVIDENCE / 'install.json').is_file(), 'Requires the disposable installation fixture.'
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(mode=0o755)
    try:
        candidate_version = None
        packages = build_candidate()
        for package in packages.glob('arch-niri-dux-[0-9]*.pkg.tar.zst'):
            candidate_version = subprocess.check_output(['pacman', '-Qp', str(package)], text=True).split()[1]
        assert candidate_version, 'Candidate arch-niri-dux package was not built.'
        use_fixture_repository(packages)
        before = {
            'packages': installed().splitlines(),
            'candidate': candidate_version,
            'generation': (migrations.STATE / 'generation').read_text().strip(),
            'checkupdates': shutil.which('checkupdates'),
            'fakeroot': shutil.which('fakeroot'),
        }
        (WORK / 'before.json').write_text(json.dumps(before, indent=2) + '\n')
        # The shell's own check entry point, which asks the server to refresh.
        reply = user_command('dms', 'ipc', 'call', 'systemupdater', 'updatestatus', capture=True).stdout
        assert 'SUCCESS' in reply, reply

        def found():
            state = daemon_request('sysupdate.getState')
            if state['phase'] != 'idle':
                return None
            return next(
                (
                    package
                    for package in state['packages']
                    if package['name'] == 'arch-niri-dux' and package['toVersion'] == candidate_version
                ),
                None,
            )

        try:
            update = wait_until('Dux update check finding the candidate', found, timeout=120)
        except TimeoutError:
            (EVIDENCE / 'dux-update-state.json').write_text(json.dumps(daemon_request('sysupdate.getState'), indent=2))
            raise
        state = daemon_request('sysupdate.getState')
        before['found'] = update
        before['backends'] = state['backends']
        (WORK / 'before.json').write_text(json.dumps(before, indent=2) + '\n')
        # The same parameters SystemUpdateService.runUpdates sends by default.
        daemon_request('sysupdate.upgrade', {'includeFlatpak': True, 'includeAUR': True, 'terminal': '', 'ignored': []})
        wait_until(
            'Dux starting pkexec',
            lambda: subprocess.run(['pgrep', '-x', 'pkexec'], stdout=subprocess.DEVNULL, check=False).returncode == 0,
        )
    except BaseException:
        restore_pacman_conf()
        raise


def check():
    try:
        before = json.loads((WORK / 'before.json').read_text())

        def finished():
            state = daemon_request('sysupdate.getState')
            assert state['phase'] != 'error', state
            return state['phase'] == 'idle' and installed().splitlines() != before['packages'] and state

        try:
            state = wait_until('Dux upgrade transaction', finished, timeout=300)
        except (TimeoutError, AssertionError):
            (EVIDENCE / 'dux-update-state.json').write_text(json.dumps(daemon_request('sysupdate.getState'), indent=2))
            raise
        after = installed().splitlines()
        assert f'arch-niri-dux {before["candidate"]}' in after, after
        name = f'99999999_{FIXTURE_NUMBER:02}_verification.sh'
        wait_until('System migration from the ALPM hook', lambda: (migrations.STATE / (name + '.json')).is_file())
        assert SYSTEM_EFFECT.read_text().splitlines() == ['dux-updater']
        generation = (migrations.STATE / 'generation').read_text().strip()
        assert generation != before['generation']
        wait_until(
            'User migration from the session .path unit',
            lambda: (migrations.USER_STATE / (name + '.json')).is_file(),
        )
        assert USER_EFFECT.read_text().splitlines() == ['dux-updater']
        journal = subprocess.check_output(
            ['journalctl', '-b', '--no-pager', '-o', 'cat', '_COMM=pkexec'], text=True
        ).strip()
        (EVIDENCE / 'dux-update.json').write_text(
            json.dumps(
                {
                    'trigger': 'DMS server sysupdate.refresh via shell IPC, then sysupdate.upgrade',
                    'check_mechanism': 'checkupdates' if before['checkupdates'] else 'fakeroot private database',
                    'backends': before['backends'],
                    'found': before['found'],
                    'before': before['packages'],
                    'after': after,
                    'upgrade_log': state.get('recentLog', []),
                    'pkexec_journal': journal.splitlines(),
                    'polkit_prompt_answered_in_session': True,
                    'system_migration_recorded': True,
                    'generation': {'before': before['generation'], 'after': generation},
                    'user_migration_via_path_unit': True,
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        restore_pacman_conf()
    SYSTEM_EFFECT.unlink()
    USER_EFFECT.unlink()
    shutil.rmtree(WORK)


def screen_shows(path, phrase):
    text = subprocess.check_output(
        ['tesseract', str(path), 'stdout', '--psm', '11'], text=True, stderr=subprocess.DEVNULL
    )
    return phrase in text.lower()


def verify(vm, passed):
    vm.probe('dux_update', 'prepare', timeout=600)
    # Dux's polkit agent opens its authentication dialog for pkexec.
    deadline = time.monotonic() + 60
    while True:
        vm.probe('dux_update', 'prompt-screenshot')
        if screen_shows(vm.directory / PROMPT, 'authentication'):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f'Dux authentication dialog did not appear; see {PROMPT}.')
        time.sleep(2)
    vm.type_graphical(PASSWORD)
    vm.key('ret')
    vm.probe('dux_update', 'check', timeout=420)
    passed("Dux's updater found and installed a release whose system and user migrations ran")


if __name__ == '__main__':
    dispatch(
        'dux_update',
        {
            'prepare': prepare,
            'prompt-screenshot': lambda: screenshot(PROMPT),
            'check': check,
        },
    )

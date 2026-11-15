"""Ordered acceptance stages and continuation policy."""

import time
from tools.verification import apps, boot, browser, camera, desktop, dux_update, pdf, recovery, suspend
from tools.verification.payload import probe


def _policy(vm, passed):
    probe(vm, 'policy', 'check', timeout=180)
    passed('Package presets, systemd policy and PAM-unlocked keyring')


def _network(vm, passed):
    probe(vm, 'network', 'check', timeout=240)
    passed('Inbound firewall and resolve-only mDNS')


def guest_alive(vm, timeout=30):
    try:
        if vm.process is None or vm.process.poll() is not None:
            return False
        vm.command('true', timeout=timeout)
        return True
    except Exception:
        return False


def run_stages(vm, passed, stages):
    """Run (name, func) stages in order, continuing past failures.

    Continues while the guest answers. Boot or desktop failing stops the run,
    since later stages cannot mean anything without them.
    Returns (failures, skipped).
    """
    failures = {}
    skipped = []
    for index, (name, func) in enumerate(stages):
        start = time.monotonic()
        try:
            func(vm, passed)
        except Exception as error:
            failures[name] = error
            print(f'FAIL: {name} after {time.monotonic() - start:.0f}s: {error}', flush=True)
            if name in ('boot', 'desktop') or not guest_alive(vm):
                skipped.extend(n for n, _ in stages[index + 1 :])
                break
            continue
        print(f'DONE: {name} in {time.monotonic() - start:.0f}s', flush=True)
    return failures, skipped


def full_stages(args):
    slow = args.slow
    stages = [
        ('boot', lambda vm, passed: boot.verify_boot(vm, passed, args.swap_gib)),
        ('policy', _policy),
        ('network', _network),
        ('desktop', desktop.verify),
        ('apps', apps.verify),
        ('pdf', pdf.verify),
        ('camera', camera.verify),
        ('browser', browser.verify),
        ('lock', desktop.verify_lock),
        ('suspend', suspend.verify),
        ('dux_update', dux_update.verify),
        ('recovery', recovery.verify),
        ('fallback', boot.verify_fallback),
    ]
    if not slow:
        stages = [(name, func) for name, func in stages if name != 'dux_update']
    return stages

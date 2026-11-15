#!/usr/bin/env python3
"""Qualify the current branch with the AGENTS.md tier, logging each step.

Picks the tier from the files changed against origin/main, runs the tier's
make targets sequentially with one log per step under out/qualify/<head>/,
and writes out/qualify/<head>/EVIDENCE.md for the PR. Refuses on a dirty
tree so the evidence belongs to one commit.
"""

import argparse
import fnmatch
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = 'origin/main'
ALONGSIDE_HOME = Path.home() / '.local/share/arch-niri-dux/alongside'

# (tier, path patterns, make steps in run order). A trailing '/' matches the
# whole tree; other patterns match the full path or the bare file name.
TIERS = (
    ('docs', ('*.md', '*.yml', '*.yaml', '.github/'), ['lint']),
    (
        'scripts',
        ('scripts/', 'tests/', 'tools/dev/', 'Makefile', 'flake.*', '*.toml'),
        ['lint', 'test'],
    ),
    ('desktop', ('desktop/', 'packages/'), ['lint', 'test', 'verify-fast']),
    ('probe', ('tools/verification/', 'tools/vm/'), ['lint', 'test', 'verify']),
    (
        'installer',
        (
            'installer/',
            'archiso/',
            'tools/build/',
            'tools/release/',
            'tools/fixtures/',
            'tools/packages/',
            'tools/workspace/',
            'keys/',
            'packages/RELEASING.md',
        ),
        ['lint', 'test', 'iso-development', 'verify'],
    ),
)
TIER_NAMES = tuple(name for name, _, _ in TIERS)
TIER_STEPS = {name: list(tier_steps) for name, _, tier_steps in TIERS}
# Installer storage and alongside code need the real dual-boot runs.
ALONGSIDE_PATTERNS = ('installer/storage.py', 'tools/fixtures/', '*alongside*')
ALONGSIDE_STEPS = ['alongside-linux', 'alongside-windows']
# VM steps take an evidence directory and leave a marker file on success.
STEP_MARKERS = {'verify': 'ACCEPTANCE-PASSED', 'verify-fast': 'FAST-PASSED'}


@dataclass
class StepResult:
    step: str
    argv: list
    code: int
    seconds: float


def _matches(patterns, path):
    name = path.rsplit('/', 1)[-1]
    for pattern in patterns:
        if pattern.endswith('/'):
            if path == pattern.rstrip('/') or path.startswith(pattern):
                return True
        elif fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def _tier_index(path):
    return max(
        (index for index, (_, patterns, _) in enumerate(TIERS) if _matches(patterns, path)),
        default=TIER_NAMES.index('scripts'),
    )


def needs_alongside(files):
    """True when the installer's storage or alongside code changed."""
    return any(_matches(ALONGSIDE_PATTERNS, path) for path in files)


def decide(files, tier_override=None):
    """Pick (tier, reason, steps) for changed files.

    An override picks the base tier; the alongside rule still applies, since it
    follows the files changed rather than the tier choice.
    """
    if tier_override is not None:
        tier, reason = tier_override, f'explicit --tier {tier_override}'
    elif not files:
        tier, reason = 'scripts', f'no files changed vs {BASE}; default scripts tier'
    else:
        top = max(_tier_index(path) for path in files)
        headliners = sorted({path for path in files if _tier_index(path) == top})[:3]
        tier = TIER_NAMES[top]
        reason = f'{", ".join(headliners)} needs the {tier} tier'
    if needs_alongside(files) and tier != 'installer':
        tier = 'installer'
        reason += '; alongside code changed, qualifying at the installer tier'
    steps = list(TIER_STEPS[tier])
    if needs_alongside(files):
        steps += ALONGSIDE_STEPS
        reason += '; adding Debian then Windows alongside runs'
    return tier, reason, steps


def _display(path):
    """Repo-relative path for evidence a worker pastes into a PR."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def git(*args):
    try:
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True, cwd=ROOT).stdout
    except subprocess.CalledProcessError:
        raise SystemExit(f"cannot run git {' '.join(args)}; run 'git fetch origin' and retry")


def check_clean(status):
    """Refuse unless the tree is clean, so the evidence belongs to one commit."""
    if status.strip():
        raise SystemExit(
            'refusing: uncommitted changes present; commit or stash them so the evidence belongs to one commit'
        )


def changed_files(base=None):
    return git('diff', '--name-only', f'{base or BASE}...HEAD').splitlines()


def check_fixtures(home=ALONGSIDE_HOME):
    """Return alongside fixture paths, refusing with a clear message when any are missing."""
    wanted = {
        'linux fixture': home / 'linux-fixture/disk.qcow2',
        'linux firmware': home / 'linux-fixture/OVMF_VARS.fd',
        'linux READY': home / 'linux-fixture/READY',
        'windows fixture': home / 'windows/disk.qcow2',
        'windows firmware': home / 'windows/OVMF_VARS.fd',
        'windows report token': home / 'windows/report-token',
        'windows READY': home / 'windows/READY',
    }
    missing = sorted(name for name, path in wanted.items() if not path.is_file())
    if not (home / 'windows/tpm').is_dir():
        missing = sorted(missing + ['windows TPM state'])
    if missing:
        raise SystemExit(
            f'alongside fixtures missing under {home}: {", ".join(missing)}; '
            'provision them per ALONGSIDE.md before qualifying alongside changes'
        )
    return wanted


def newest_iso():
    """Newest built ISO in out/, or None when iso-development has not run yet."""
    candidates = sorted(
        (path for path in (ROOT / 'out').glob('*.iso') if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def command_for(step, evidence_dir, forward, iso=None, fixtures=None):
    """Build the make argv for a step; alongside commands need the fresh ISO."""
    if step in ('lint', 'test', 'iso-development'):
        return ['make', step]
    args = list(forward)
    if not any(arg == '--directory' or arg.startswith('--directory=') for arg in args):
        args += ['--directory', str(evidence_dir / step)]
    if step in ('verify-fast', 'verify'):
        target = step
    elif step in ALONGSIDE_STEPS:
        if iso is None:
            iso = '<ISO built by iso-development above>'
        os_name = step.split('-')[1]
        target = 'verify-alongside'
        args = [
            '--os',
            os_name,
            '--iso',
            str(iso),
            '--fixture',
            str(fixtures[f'{os_name} fixture']),
            '--firmware',
            str(fixtures[f'{os_name} firmware']),
            *(['--windows-token', str(fixtures['windows report token'])] if os_name == 'windows' else []),
            *args,
        ]
    else:
        raise SystemExit(f'unknown qualify step: {step}')
    argv = ['make', target]
    if args:
        argv.append(f'ARGS={shlex.join(args)}')
    return argv


def run_command(argv, log_path):
    """Run argv, teeing output to the step log and stdout; return the exit code."""
    with open(log_path, 'w') as log:
        process = subprocess.Popen(
            [str(arg) for arg in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            sys.stdout.write(line)
        return process.wait()


def execute(steps, evidence_dir, forward, fixtures, runner=run_command, resolve_iso=newest_iso):
    """Run steps sequentially, stopping at the first failure."""
    results = []
    for step in steps:
        iso = resolve_iso() if step in ALONGSIDE_STEPS else None
        if step in ALONGSIDE_STEPS and iso is None:
            raise SystemExit('no ISO in out/; iso-development should have built one for the alongside runs')
        argv = command_for(step, evidence_dir, forward, iso=iso, fixtures=fixtures)
        start = time.monotonic()
        code = runner(argv, evidence_dir / f'{step}.log')
        results.append(StepResult(step, argv, code, time.monotonic() - start))
        if code != 0:
            break
    return results


def format_plan(sha, tier, reason, steps, evidence_dir, forward, fixtures):
    lines = [f'commit {sha} (vs {BASE})', f'tier: {tier} ({reason})', f'evidence: {_display(evidence_dir)}/', 'steps:']
    # The alongside ISO does not exist until iso-development runs; never print a stale one.
    iso = None if 'iso-development' in steps else newest_iso()
    lines += [f'  {shlex.join(command_for(step, evidence_dir, forward, iso=iso, fixtures=fixtures))}' for step in steps]
    return '\n'.join(lines)


def write_evidence(evidence_dir, sha, tier, reason, results, end=None):
    """Write the short Markdown summary a worker pastes into the PR."""
    total = sum(result.seconds for result in results)
    failed = next((result for result in results if result.code != 0), None)
    if failed is None:
        outcome = f'PASS, {len(results)} steps in {total:.0f}s'
    else:
        outcome = f'FAIL at {failed.step} (exit {failed.code}) after {total:.0f}s'
    lines = [
        '# Qualification evidence',
        '',
        f'- Commit: {sha}',
        f'- Tier: {tier} ({reason})',
        f'- Result: {outcome}',
        '',
        '## Steps',
        '',
        *(f'- `{shlex.join(result.argv)}` → exit {result.code} in {result.seconds:.0f}s' for result in results),
        '',
        '## Evidence',
        '',
        f'- Step logs: {_display(evidence_dir)}/',
    ]
    for result in results:
        marker = STEP_MARKERS.get(result.step, 'SUCCESS' if result.step in ALONGSIDE_STEPS else None)
        if marker is None:
            continue
        present = (evidence_dir / result.step / marker).is_file()
        lines.append(f'- {_display(evidence_dir / result.step)} ({marker} {"present" if present else "missing"})')
    if end is not None and end != sha:
        lines += ['', f'HEAD moved during qualification ({sha} -> {end}); this evidence spans two commits.']
    (evidence_dir / 'EVIDENCE.md').write_text('\n'.join(lines) + '\n')


def prepare_evidence_dir(evidence_dir):
    """Create the run's evidence root, refusing a rerun over a previous attempt's evidence."""
    if evidence_dir.exists() and any(evidence_dir.iterdir()):
        raise SystemExit(f'{_display(evidence_dir)}/ already holds evidence; remove it to requalify this commit')
    evidence_dir.mkdir(parents=True, exist_ok=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tier', choices=TIER_NAMES, help='run one tier instead of picking from changed files')
    parser.add_argument('--dry-run', action='store_true', help='print the plan without running anything')
    return parser.parse_known_args(argv)


def main(argv=None):
    args, forward = parse_args(argv)
    check_clean(git('status', '--porcelain'))
    sha = git('rev-parse', 'HEAD').strip()
    files = changed_files()
    tier, reason, steps = decide(files, args.tier)
    evidence_dir = ROOT / 'out' / 'qualify' / sha
    fixtures = check_fixtures() if any(step in ALONGSIDE_STEPS for step in steps) else None
    if args.dry_run:
        print(format_plan(sha, tier, reason, steps, evidence_dir, forward, fixtures))
        return 0
    prepare_evidence_dir(evidence_dir)
    results = execute(steps, evidence_dir, forward, fixtures)
    end = git('rev-parse', 'HEAD').strip()
    write_evidence(evidence_dir, sha, tier, reason, results, end=end)
    if end != sha:
        raise SystemExit(f'HEAD moved during qualification ({sha} -> {end}); see {evidence_dir}/EVIDENCE.md')
    failed = next((result for result in results if result.code != 0), None)
    return failed.code if failed is not None else 0


if __name__ == '__main__':
    raise SystemExit(main())

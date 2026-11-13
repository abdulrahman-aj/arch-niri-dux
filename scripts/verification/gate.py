"""Error gate: fail verification on guest warnings/errors without a reviewed allowlist entry.

The gate scans only the final clean boot's journal (``journalctl -b``), so
deliberate-failure probes from earlier boots (the firewall containment probe,
the tampered-database signature rejection) never reach it.
"""

import re
from pathlib import Path

ALLOWLIST = Path(__file__).with_name('gate-allowlist.txt')
JOURNAL_FILE = 'guest-journal-warning.log'
FAILED_FILES = ('guest-failed-units.txt', 'guest-failed-units-user.txt')
UNMATCHED_FILE = 'gate-unmatched.log'

ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b\(.\)|\x1b[@-Z\\-_]')
MONTHS = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'


def normalize(line):
    """Apply the inventory's normalization rules to one log line."""
    line = ANSI_RE.sub('', line).rstrip('\n')
    line = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?', '<TS>', line)
    line = re.sub('(' + MONTHS + r')(\s+\d{1,2}) \d{2}:\d{2}:\d{2}', r'\1\2 <TS>', line)
    line = re.sub(r'\[\s*\d+\.\d+\]', '[<TS>]', line)
    line = re.sub(r'\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b', '<TS>', line)
    line = re.sub(r'\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b', '<UUID>', line)
    line = re.sub(r'\b0[xX][0-9a-fA-F]+\b', '<HEX>', line)
    line = re.sub(r'\b(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{12,}\b', '<HASH>', line)
    line = re.sub(r'([A-Za-z0-9_][\w.+-]*)\[(\d+)\]', r'\1[<PID>]', line)
    line = re.sub(r'\(([\w.+-]+):(\d+)\)', r'(\1:<PID>)', line)
    line = re.sub(r'\bpid[= ](\d+)\b', 'pid <PID>', line, flags=re.IGNORECASE)
    line = re.sub(r'((?:\.qml|\.vala|\.c|\.js|\.py|\.rs):)\d+(?::\d+)?', r'\1<LINE>', line)
    return re.sub(r'\b\d{5,}\b', '<NUM>', line)


def load_allowlist(path=ALLOWLIST):
    """Parse the allowlist: one regex per line, each with a trailing ``# reason``."""
    entries = []
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        pattern, separator, reason = raw.rpartition(' # ')
        if not separator or not pattern.strip() or not reason.strip():
            raise ValueError(f'{path}:{number}: allowlist line needs "REGEX # reason": {raw!r}')
        entries.append((re.compile(pattern), reason.strip()))
    if not entries:
        raise ValueError(f'{path}: allowlist is empty')
    return entries


def collect(vm, directory):
    """Capture the final boot's warnings and failed units into the evidence dir."""
    directory = Path(directory)
    vm.command(f'journalctl -b -p warning --no-pager > /run/verify/{JOURNAL_FILE}')
    vm.command(f'systemctl --failed --no-legend > /run/verify/{FAILED_FILES[0]}')
    vm.command(
        'uid=$(id -u tester); runuser -u tester -- env '
        'XDG_RUNTIME_DIR=/run/user/$uid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus '
        f'systemctl --user --failed --no-legend > /run/verify/{FAILED_FILES[1]}'
    )
    return [directory / JOURNAL_FILE, *[directory / name for name in FAILED_FILES]]


def unmatched(directory, entries=None):
    """Return [(source, raw, normalized)] for lines no allowlist entry matches."""
    directory = Path(directory)
    entries = entries if entries is not None else load_allowlist()
    found = []
    sources = [JOURNAL_FILE, *FAILED_FILES]
    for source in sources:
        path = directory / source
        if not path.is_file():
            continue
        for raw in path.read_text().splitlines():
            if not raw.strip():
                continue
            normalized = normalize(raw)
            if not any(pattern.search(normalized) for pattern, _ in entries):
                found.append((source, raw, normalized))
    return found


class GateFailure(RuntimeError):
    pass


def check(directory):
    """Fail when the final boot logged a warning/error outside the allowlist."""
    directory = Path(directory)
    lines = unmatched(directory)
    if not lines:
        return []
    report = [f'{source}: normalized: {normalized} | raw: {raw}' for source, raw, normalized in lines]
    (directory / UNMATCHED_FILE).write_text('\n'.join(report) + '\n')
    raise GateFailure(
        f'Error gate: {len(lines)} unreviewed guest warning(s)/failure(s); see {UNMATCHED_FILE}:\n' + '\n'.join(report)
    )

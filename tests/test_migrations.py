"""Behavioral regression coverage; fixtures are never packaged."""

import types
import fcntl
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / 'desktop/usr/bin/arch-niri-dux-migrate'
migrate = types.ModuleType('migrate')
exec(compile(RUNNER.read_text(), str(RUNNER), 'exec'), migrate.__dict__)


def acquire(state, connection):
    with migrate.lock(state):
        connection.send(True)
    connection.close()


def hold_exclusive(state, connection):
    with open(Path(state) / 'lock', 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        connection.send(True)
        connection.close()
        time.sleep(10)


class Migrations(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.scripts = self.root / 'scripts'
        for scope in ('system', 'user'):
            (self.scripts / scope).mkdir(parents=True)
        self.addCleanup(patch.stopall)
        patch.object(migrate, 'SCRIPTS', self.scripts).start()
        patch.object(migrate, 'SYSTEM', self.state).start()

    def script(self, name, body, scope='system'):
        path = self.scripts / scope / (name + '.sh')
        path.write_text('set -eu\n' + body + '\n')
        return path

    def test_failure_stops_then_retries_without_duplicate(self):
        first = self.root / 'first'
        second = self.root / 'second'
        third = self.root / 'third'
        ready = self.root / 'ready'
        self.script('20261106_02_failure', f'test -e {ready}\ntouch {second}')
        self.script('20261106_03_last', f'test -e {second}\ntouch {third}')
        self.script('20261106_01_first', f'touch {first}')
        with patch.object(migrate, 'trusted'):
            queue = migrate.scripts('system')
            with self.assertRaises(RuntimeError):
                migrate.execute(queue, self.state)
            self.assertEqual(len(migrate.pending(queue, self.state)), 2)
            timestamp = first.stat().st_mtime_ns
            self.assertFalse(third.exists())
            ready.touch()
            migrate.execute(queue, self.state)
        self.assertEqual(first.stat().st_mtime_ns, timestamp)
        self.assertTrue(third.exists())

    def test_skip_requires_reason(self):
        path = self.script('20261106_01_skip', 'echo customized; exit 77')
        migrate.execute([path], self.state)
        record = json.loads((self.state / (path.name + '.json')).read_text())
        self.assertEqual(record['outcome'], 'skip')
        self.assertTrue(record['reason'])
        bad = self.script('20261106_02_bad', 'exit 77')
        with self.assertRaises(RuntimeError):
            migrate.execute([bad], self.state)

    def test_unsortable_name_rejected(self):
        self.script('20261106_01_valid', 'true')
        self.script('9_fix', 'true')
        with patch.object(migrate, 'trusted'):
            with self.assertRaises(RuntimeError):
                migrate.scripts('system')
            (self.scripts / 'system' / '9_fix.sh').unlink()
            self.assertEqual(len(migrate.scripts('system')), 1)

    def test_shared_lock_blocks_system_work(self):
        (self.state / 'lock').touch()
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        with migrate.lock(self.state, shared=True):
            process = context.Process(target=acquire, args=(self.state, send))
            process.start()
            self.addCleanup(lambda: process.kill() if process.is_alive() else None)
            self.assertFalse(receive.poll(0.2))
        self.assertTrue(receive.poll(3))
        process.join(3)
        self.assertEqual(process.exitcode, 0)

    def test_stale_transaction_lock_is_bounded(self):
        lock = self.root / 'db.lck'
        lock.touch()
        with patch.object(migrate, 'PACMAN_LOCK', lock), patch.object(migrate, 'TRANSACTION_WAIT', 0):
            with self.assertRaises(RuntimeError):
                migrate.process('run', 'system', self.state)
        self.assertFalse((self.state / 'generation').exists())

    def test_contended_migration_lock_fails_with_recorded_failure(self):
        (self.state / 'lock').touch()
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        holder = context.Process(target=hold_exclusive, args=(self.state, send))
        holder.start()
        self.addCleanup(lambda: holder.kill() if holder.is_alive() else None)
        self.assertTrue(receive.poll(5))
        previous_umask = migrate.os.umask(0o022)
        self.addCleanup(migrate.os.umask, previous_umask)
        patch.object(migrate, 'LOCK_WAIT', 2).start()
        patch.object(migrate, 'TRANSACTION_WAIT', 0).start()
        patch.object(migrate, 'PACMAN_LOCK', self.root / 'no-db.lck').start()
        patch.object(migrate, 'trusted').start()
        notifier = patch.object(migrate, 'notify').start()
        patch.object(migrate.sys, 'argv', ['arch-niri-dux-migrate', 'run']).start()
        patch.object(migrate.os, 'geteuid', return_value=0).start()
        start = time.monotonic()
        result = migrate.main()
        elapsed = time.monotonic() - start
        if holder.is_alive():
            holder.kill()
        holder.join(5)
        self.assertEqual(result, 1)
        failure = (self.state / 'failure').read_text().strip()
        self.assertIn('lock', failure.lower())
        notifier.assert_called_once()
        # The run failed at the deadline instead of hanging on the holder.
        self.assertLess(elapsed, 10)

    def test_fresh_user_baseline_while_system_pending(self):
        (self.state / 'lock').touch()
        user_state = self.root / 'fresh-user'
        user_state.mkdir()
        effect = self.root / 'unexpected'
        path = self.script('20261106_01_user', f'touch {effect}', scope='user')
        with patch.object(migrate, 'trusted'):
            migrate.process('baseline', 'user', user_state)
        self.assertFalse(effect.exists())
        self.assertEqual(migrate.pending([path], user_state), [])

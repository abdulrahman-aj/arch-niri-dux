"""Behavioral regression coverage; fixtures are never packaged."""

from contextlib import contextmanager
import importlib.machinery
import importlib.util
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / 'desktop/usr/bin/arch-niri-dux-migrate'
loader = importlib.machinery.SourceFileLoader('migrate', str(RUNNER))
spec = importlib.util.spec_from_loader(loader.name, loader)
migrate = importlib.util.module_from_spec(spec)
loader.exec_module(migrate)


def acquire(state, connection):
    with migrate.lock(state):
        connection.send(True)
    connection.close()


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

    @contextmanager
    def queue(self):
        # Host fixtures are user-owned; production ownership rejection is tested
        # separately. Execute actual scripts and persist actual records here.
        with patch.object(migrate, 'trusted'):
            yield migrate.scripts('system')

    def test_failure_stops_then_retries_without_duplicate(self):
        first = self.root / 'first'
        second = self.root / 'second'
        third = self.root / 'third'
        ready = self.root / 'ready'
        self.script('02_failure', f'test -e {ready}\ntouch {second}')
        self.script('03_last', f'test -e {second}\ntouch {third}')
        self.script('01_first', f'touch {first}')
        with self.queue() as queue:
            with self.assertRaises(RuntimeError):
                migrate.execute(queue, self.state)
            self.assertEqual(len(migrate.pending(queue, self.state)), 2)
            timestamp = first.stat().st_mtime_ns
            self.assertFalse(third.exists())
            ready.touch()
            migrate.execute(queue, self.state)
        self.assertEqual(first.stat().st_mtime_ns, timestamp)
        self.assertTrue(third.exists())

    def test_skip_requires_reason_and_baseline_has_no_effects(self):
        path = self.script('01_skip', 'echo customized; exit 77')
        migrate.execute([path], self.state)
        record = json.loads((self.state / (path.name + '.json')).read_text())
        self.assertEqual(record['outcome'], 'skip')
        self.assertTrue(record['reason'])
        bad = self.script('02_bad', 'exit 77')
        with self.assertRaises(RuntimeError):
            migrate.execute([bad], self.state)
        effect = self.root / 'unexpected'
        fresh = self.script('03_fresh', f'touch {effect}')
        migrate.execute([fresh], self.state, baseline=True)
        self.assertFalse(effect.exists())

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

    def test_user_execution_is_gated_and_records_are_separate(self):
        (self.state / 'lock').touch()
        user_state = self.root / 'user-state'
        user_state.mkdir()
        effect = self.root / 'user-effect'
        path = self.script('01_user', f'touch {effect}', scope='user')
        with patch.object(migrate, 'trusted'):
            with self.assertRaises(RuntimeError):
                migrate.process('run', 'user', user_state)
            self.assertFalse(effect.exists())
            (self.state / 'generation').touch()
            migrate.process('run', 'user', user_state)
            self.assertTrue(effect.exists())
            self.assertEqual(migrate.pending([path], user_state), [])
            self.assertEqual(migrate.pending([path], self.state), [path])
            path.unlink()
            self.assertTrue(migrate.system_ready())
            (self.state / 'failure').touch()
            self.assertFalse(migrate.system_ready())

    def test_stale_transaction_lock_is_bounded(self):
        lock = self.root / 'db.lck'
        lock.touch()
        with patch.object(migrate, 'PACMAN_LOCK', lock), patch.object(migrate, 'TRANSACTION_WAIT', 0):
            with self.assertRaises(RuntimeError):
                migrate.process('run', 'system', self.state)
        self.assertFalse((self.state / 'generation').exists())

    def test_notification_once_per_failure_until_recovery(self):
        with patch.object(migrate.os, 'geteuid', return_value=1000), patch.object(migrate.subprocess, 'run') as send:
            migrate.notify('first failure', self.state)
            migrate.notify('first failure', self.state)
            self.assertEqual(send.call_count, 1)
            migrate.notify('second failure', self.state)
            self.assertEqual(send.call_count, 2)

    def test_fresh_user_baseline_while_system_pending(self):
        (self.state / 'lock').touch()
        user_state = self.root / 'fresh-user'
        user_state.mkdir()
        effect = self.root / 'unexpected'
        path = self.script('01_user', f'touch {effect}', scope='user')
        with patch.object(migrate, 'trusted'):
            migrate.process('baseline', 'user', user_state)
        self.assertFalse(effect.exists())
        self.assertEqual(migrate.pending([path], user_state), [])

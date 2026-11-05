import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.verification import guest as guest


class RestorePromptTests(unittest.TestCase):
    def run_child(self, source, replies, timeout=5):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / 'interactive.py'
            program.write_text(source)
            with contextlib.redirect_stdout(io.StringIO()):
                guest.run_interactive([sys.executable, str(program)], replies, timeout=timeout)

    def test_answers_follow_each_prompt_without_prefetch_loss(self):
        self.run_child('''import os, sys, time
for prompt, answer in [(b'Choice: ', b'r\\n'), (b'Description: ', b'backup\\n'), (b'Reboot? ', b'n\\n')]:
    os.write(1, prompt[:3])
    time.sleep(0.01)
    os.write(1, prompt[3:])
    # Reproduce upstream's fresh-reader behavior: consume all available bytes,
    # retaining only this answer. Future answers sent early would be lost.
    if os.read(0, 4096) != answer:
        sys.exit(12)
''', [(b'Choice: ', b'r\n'), (b'Description: ', b'backup\n'), (b'Reboot? ', b'n\n')])

    def test_success_without_expected_interaction_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.run_child('print("no-op", flush=True)', [(b'Choice: ', b'r\n')])

    def test_failed_restore_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.run_child('raise SystemExit(3)', [])

    def test_unexpected_prompt_times_out_and_child_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'pid'
            source = f'''import os
from pathlib import Path
Path({str(pidfile)!r}).write_text(str(os.getpid()))
print('Unexpected prompt: ', flush=True)
os.read(0, 1)
'''
            with self.assertRaises(TimeoutError):
                self.run_child(source, [(b'Choice: ', b'r\n')], timeout=0.5)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)

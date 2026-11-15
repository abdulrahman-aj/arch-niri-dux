"""Exercise module execution, where imports below the main guard are too late."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from textwrap import dedent
import unittest


class ProbeEntrypointTests(unittest.TestCase):
    def run_entrypoint(self, module, arguments, cache):
        program = dedent("""\
            import runpy
            import sys
            from tools.vm import control
            from tools.workspace import resources

            class VM:
                commands = 0
                def __init__(self, *args): pass
                def start(self, **kwargs): pass
                def expect(self, *args, **kwargs): pass
                def attach(self): pass
                def stop(self): pass
                def command(self, script, timeout=120):
                    VM.commands += 1
                    return ''

            control.VM = VM
            resources.disk_guard = lambda *args, **kwargs: None
            resources.heavy_guard = lambda *args, **kwargs: None
            module = sys.argv[1]
            sys.argv = [module, *sys.argv[2:]]
            runpy.run_module(module, run_name='__main__')
            assert VM.commands > 0
            """)
        subprocess.run(
            [sys.executable, '-c', program, module, *arguments],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ | {'XDG_CACHE_HOME': str(cache)},
            capture_output=True,
            text=True,
            check=True,
        )

    def test_console_probe_works_when_executed_as_a_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.sockets').mkdir()
            self.run_entrypoint(
                'tools.vm.console', ['--directory', str(root), 'probe', 'policy', 'check'], root / 'cache'
            )

    def test_installer_preview_mounts_payload_when_executed_as_a_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            iso = root / 'development.iso'
            iso.touch()
            self.run_entrypoint(
                'tools.dev.installer_preview',
                ['--directory', str(root / 'preview'), '--iso', str(iso)],
                root / 'cache',
            )
            self.assertTrue((root / 'preview/installer-preview.json').is_file())

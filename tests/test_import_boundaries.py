from pathlib import Path
import subprocess
import sys
import unittest

from tools.dev.lint_imports import violations


class ImportBoundaryTests(unittest.TestCase):
    def test_forbidden_imports_fail_including_relative_and_from_forms(self):
        for path, source in (
            ('tools/packages/example.py', 'import tools.release.key'),
            ('tools/packages/example.py', 'from tools import verification'),
            ('tools/packages/example.py', 'from ..build import payload'),
            ('tools/build/example.py', 'from tools.release import key'),
            ('installer/example.py', 'import tools.digest'),
            ('installer/wizard.py', 'import install'),
            ('installer/wizard.py', 'from . import install'),
            ('installer/storage.py', 'from . import wizard'),
            ('installer/storage.py', 'import install'),
            ('installer/payload.py', 'from .install import TARGET'),
            ('tools/verification/probes/boot.py', 'from ..login import installed_login'),
            ('tools/verification/probes/guest.py', 'from tools.vm.control import VM'),
            ('tools/verification/boot.py', 'from .probes import boot'),
            ('tools/verification/images.py', 'from .login import installed_login'),
            ('tools/verification/scenarios/release.py', 'from ..session import run_install'),
            ('tools/verification/scenarios/install.py', 'from .. import verify_alongside'),
            ('tools/verification/stages.py', 'from .verify_hibernate import main'),
            ('tools/vm/control.py', 'from tools.verification.payload import probe'),
            ('tools/workspace/state.py', 'from . import gc'),
            ('tools/workspace/fixtures.py', 'from tools.workspace.gc import collect'),
            ('tools/workspace/resources.py', 'from . import __main__'),
            ('tools/packages/example.py', 'import tools'),
            ('tools/build/example.py', 'import tools'),
            ('tools/packages/example.py', 'from ..verification import guest'),
            ('tools/build/example.py', 'from tools import release'),
        ):
            with self.subTest(path=path, source=source):
                self.assertTrue(list(violations(Path(path), source)))

    def test_shared_digest_and_local_imports_are_allowed(self):
        source = 'from tools.digest import digest\nfrom . import inputs\nimport pathlib'
        self.assertEqual(list(violations(Path('tools/packages/example.py'), source)), [])
        source = 'from .guest import run\nfrom ..images import png_dimensions'
        self.assertEqual(list(violations(Path('tools/verification/probes/boot.py'), source)), [])
        source = 'from .state import registered_paths\nfrom .fixtures import accepted_fixtures'
        self.assertEqual(list(violations(Path('tools/workspace/gc.py'), source)), [])
        source = 'from . import payload, storage, wizard\nimport payload\nimport storage\nimport wizard'
        self.assertEqual(list(violations(Path('installer/install.py'), source)), [])
        source = 'from . import storage\nimport storage'
        self.assertEqual(list(violations(Path('installer/wizard.py'), source)), [])

    def test_only_storage_runs_disk_writing_commands(self):
        for path, source in (
            ('installer/install.py', "run('mkfs.btrfs', '--force', mapped)"),
            ('installer/install.py', "run('cryptsetup', 'close', MAPPER)"),
            ('installer/install.py', "run('btrfs', 'subvolume', 'create', str(target / volume))"),
            ('installer/wizard.py', "subprocess.run(['umount', '--recursive', str(TARGET)], check=False)"),
            ('installer/payload.py', "subprocess.run(('sgdisk', '--zap-all', disk))"),
            ('installer/install.py', "subprocess.run('wipefs --all /dev/vda', shell=True)"),
        ):
            with self.subTest(path=path, source=source):
                self.assertTrue(list(violations(Path(path), source)))
                self.assertEqual(list(violations(Path('installer/storage.py'), source)), [])
        harmless = "run('blkid', '-s', 'UUID', device)\nprint('mount the disk')\nshutil.which('sgdisk')"
        self.assertEqual(list(violations(Path('installer/install.py'), harmless)), [])


class ArtifactImportTests(unittest.TestCase):
    def test_artifact_consumer_does_not_load_publication_or_probes(self):
        subprocess.run(
            [
                sys.executable,
                '-c',
                'import sys; import tools.packages.artifacts; '
                'forbidden = [name for name in sys.modules '
                'if name.startswith(("tools.release", "tools.verification"))]; '
                'assert not forbidden, forbidden',
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
        )

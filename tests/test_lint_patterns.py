from pathlib import Path
import textwrap
import unittest

from tools.dev.lint_patterns import violations


def flagged(source):
    return list(violations(Path('tools/example.py'), textwrap.dedent(source)))


def flagged_probe(source):
    return list(violations(Path('tools/verification/probes/example.py'), textwrap.dedent(source)))


def flagged_guest(source):
    return list(violations(Path('tools/verification/probes/guest.py'), textwrap.dedent(source)))


class SwallowedBroadExceptTests(unittest.TestCase):
    def test_broad_handlers_that_drop_the_error_are_flagged(self):
        for handler in ('except Exception:', 'except BaseException:', 'except:', 'except Exception as error:'):
            with self.subTest(handler=handler):
                self.assertTrue(flagged(f'try:\n    work()\n{handler}\n    pass\n'))

    def test_handlers_that_reraise_record_or_name_specific_errors_pass(self):
        for source in (
            'try:\n    work()\nexcept Exception:\n    cleanup()\n    raise\n',
            'try:\n    work()\nexcept Exception as error:\n    log(error)\n',
            'try:\n    work()\nexcept (OSError, RuntimeError):\n    pass\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged(source), [])

    def test_inner_handler_is_judged_on_its_own(self):
        source = """
            try:
                work()
            except Exception as error:
                try:
                    screenshot()
                except Exception:
                    pass
                log(error)
        """
        self.assertEqual(len(flagged(source)), 1)


class PassThroughWrapperTests(unittest.TestCase):
    def test_forward_only_bodies_are_flagged(self):
        for source in (
            'def label(disk):\n    return storage.disk_identity(disk)\n',
            'def ask(prompt, default):\n    return gum_confirm(prompt, default)\n',
            'class VM:\n    def key(self, name):\n        self.chord(name)\n',
            'def confirm(prompt, default):\n    gum_confirm(prompt, default)\n',
            'def label(disk):\n    """Describe a disk for the picker."""\n    return storage.disk_identity(disk)\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged(source)), 1)

    def test_wrappers_that_add_or_reorder_arguments_pass(self):
        for source in (
            'def label(disk):\n    return storage.disk_identity(disk, 1)\n',
            'def ask(prompt, default):\n    return gum_confirm(prompt, default_yes=default)\n',
            'def ask(default, prompt):\n    return gum_confirm(prompt, default)\n',
            'def label(disk):\n    return storage.disk_identity(disk["name"])\n',
            'def label(disk):\n    return storage.disk_identity(disk["name"] + "!")\n',
            'def version(source):\n    return version_from_text((source / ".SRCINFO").read_text(), source)\n',
            'def installed():\n    return subprocess.check_output(["pacman", "-Q", *PACKAGES], text=True).strip()\n',
            'def label(disk):\n    name = storage.disk_identity(disk)\n    return name\n',
            'def label(disk):\n    return storage.disk_identity(disk)\n    return None\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged(source), [])

    def test_decorated_and_variadic_definitions_pass(self):
        for source in (
            'import functools\n@functools.cache\ndef label(disk):\n    return storage.disk_identity(disk)\n',
            'def run(*args):\n    return subprocess.run(*args)\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged(source), [])


class ProbeLocalHelpersTests(unittest.TestCase):
    def test_evidence_writes_through_the_shared_constant_are_flagged(self):
        for source in (
            "(EVIDENCE / 'dux-update-state.json').write_text(json.dumps(state(), indent=2))",
            "Path('/run/verify/camera.json').write_text(json.dumps(state, indent=2) + '\\n')",
            "evidence.write_text(json.dumps(state, indent=2) + '\\n')",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_probe(source)), 1)

    def test_local_helper_definitions_are_flagged(self):
        for source in (
            'EVIDENCE = Path("/run/verify")',
            'def record(name, value):\n    pass',
            'def output(*args):\n    pass',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_probe(source)), 1)

    def test_same_code_in_guest_or_outside_probes_passes(self):
        source = (
            'EVIDENCE = Path("/run/verify")\n'
            'def record(name, value):\n    pass\n'
            "Path('/run/verify/a.json').write_text(json.dumps(value, indent=2) + '\\n')\n"
        )
        self.assertEqual(flagged_guest(source), [])
        self.assertEqual(flagged(source), [])

    def test_scratch_json_and_non_evidence_writes_pass(self):
        for source in (
            "(WORK / 'before.json').write_text(json.dumps(before, indent=2) + '\\n')",
            "Path('/run/systemd/system/foo.conf').write_text('[Service]\\n')",
            'payload = json.dumps(state)',
            'def record_boot():\n    pass',
            'class Probe:\n    def record(self, name):\n        pass',
            'def check():\n    def output(*args):\n        pass',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_probe(source), [])


if __name__ == '__main__':
    unittest.main()

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


def flagged_shell(source):
    return list(violations(Path('tools/verification/probes/example.py'), textwrap.dedent(source)))


def flagged_tests(source):
    return list(violations(Path('tests/test_example.py'), textwrap.dedent(source)))


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


class UnquotedShellInterpolationTests(unittest.TestCase):
    def test_interpolated_values_without_quoting_are_flagged(self):
        for source in (
            "import shlex\nvm.command(f'arch-niri-install --confirm-disk {device}')",
            "import shlex\nvm.command('sha256sum ' + device + ' > old.sha256')",
            "import shlex\nvm.command('sha256sum %s > old.sha256' % device)",
            "import shlex\nvm.command('sha256sum {} > old.sha256'.format(device))",
            "run('sh', '-c', f'userdel -r {user} 2>/dev/null || true')",
            "command = ['timeout', '3', 'bash', '-c', f'exec 3<>/dev/tcp/{address}/{PORT}']",
            "import shlex\nvm.command(f'test x = {digest(source)}')",
            "vm.command(f'systemctl --failed > /run/verify/{FAILED_FILES[0]}')",
            "import shlex\nvm.command('xdg-open ' + ' '.join(parts) + ' >/dev/null 2>&1 &')",
            "import shlex\nvm.command('sha256sum %s > old.sha256' % shlex.quote(device))",
            "vm.command(f'systemctl --failed > /run/verify/{JOURNAL_FILE}')",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_shell(source)), 1)

    def test_quoted_and_joined_interpolations_pass(self):
        for source in (
            "import shlex\nvm.command(f'arch-niri-install --confirm-disk {shlex.quote(device)}')",
            "import shlex\nvm.command('cd /run/verify && ' + shlex.join(command))",
            "import shlex\nvm.command(f'--epoch {shlex.quote(str(epoch))}')",
            "command = ['timeout', '3', 'bash', '-c', f'exec 3<>/dev/tcp/{shlex.quote(address)}/{shlex.quote(str(PORT))}']",
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_shell(source), [])

    def test_non_shell_strings_pass(self):
        for source in (
            "vm.command('sync')",
            'vm.command(script)',
            "run('useradd', '-m', '-s', '/bin/bash', user)",
            "subprocess.run(['qemu-img', 'snapshot', '-c', 'pristine', str(disk)], check=True)",
            "run('bash', '-lc', 'command -v dux-path-probe')",
            "run('tar', '-czf', package, '.PKGINFO', 'etc')",
            "run('bash', '-l', f'echo {value}')",
            "run('bash', '--login', f'echo {value}')",
            "user_command('niri', 'msg', 'action', 'spawn', '--', 'sh', '-c', 'test x')",
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_shell(source), [])

    def test_combined_shell_flags_with_unquoted_interpolation_are_flagged(self):
        for source in (
            "import shlex\nrun('bash', '-lc', f'echo {value}')",
            "import shlex\nrun('sh', '-ec', f'userdel -r {user} 2>/dev/null || true')",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_shell(source)), 1)

    def test_combined_shell_flags_with_quoted_interpolation_pass(self):
        source = "import shlex\nrun('bash', '-lc', f'echo {shlex.quote(value)}')"
        self.assertEqual(flagged_shell(source), [])

    def test_shell_code_built_by_tests_is_out_of_scope(self):
        source = "output = subprocess.check_output(['bash', '-c', f'source \"$1\"; {expression}'], text=True)"
        self.assertEqual(len(flagged_shell(source)), 1)
        self.assertEqual(flagged_tests(source), [])

    def test_shlex_quote_directly_wrapped_in_double_quotes_is_flagged(self):
        # Double quotes re-enable $(...) substitution on the quoted value.
        for source in (
            'import shlex\nvm.command(f\'printf "%s" "{shlex.quote(value)}"\')',
            "import shlex\nrun('sh', '-c', f'echo \"{shlex.join(parts)}\"')",
        ):
            with self.subTest(source=source):
                found = flagged_shell(source)
                self.assertEqual(len(found), 1)
                self.assertIn('shlex', found[0])

    def test_shlex_quote_outside_or_inside_single_quotes_passes(self):
        for source in (
            'import shlex\nvm.command(f\'printf "%s" {shlex.quote(value)}\')',
            "import shlex\nvm.command(f\"printf '%s' '{shlex.quote(value)}'\")",
            # Inside $(...) the single quotes stay syntactic, so the value is safe.
            'import shlex\nvm.command(f\'test "$(sha256sum {shlex.quote(path)} | cut -d " " -f 1)" = {shlex.quote(digest)}\')',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_shell(source), [])


class MockInteractionAssertionTests(unittest.TestCase):
    def test_mock_call_assertions_are_flagged(self):
        for source in (
            'mock.assert_called_once()',
            'mock.assert_not_called()',
            'mock.assert_called_with(7)',
            'mock.assert_any_call(1)',
            'mock.assert_has_calls([])',
            'x = mock.call_args',
            'x = mock.call_args_list',
            'x = mock.mock_calls',
            'x = mock.call_count',
            'x = mock.method_calls',
            'handler.assert_called_once_with()',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_tests(source)), 1)

    def test_outcome_assertions_pass(self):
        for source in (
            "self.assertEqual(calls, ['a'])",
            'self.assertEqual(result, 3)',
            "self.assertIn('35 GiB', message)",
            'x = call_count',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_tests(source), [])

    def test_non_test_files_are_out_of_scope(self):
        source = 'mock.assert_called_once()'
        self.assertEqual(len(flagged_tests(source)), 1)
        self.assertEqual(flagged(source), [])


class PatchedRepoFunctionTests(unittest.TestCase):
    def test_repo_function_patches_are_flagged(self):
        for source in (
            "from tools.dev import try_vm\npatch('tools.dev.try_vm.gum_input')",
            "import installer.storage as storage\npatch.object(storage, 'run')",
            "import tools.dev.try_vm\npatch.object(tools.dev.try_vm, 'gum_input')",
            "from tools.fixtures import windows\npatch.object(windows.WindowsVM, 'start')",
            "patch('tools.fixtures.windows.WindowsVM')",
            "patch('tools.verification.probes.dispatch')",
            "from installer import install as installer\npatch('installer.storage.run')",
            "from installer import storage\npatch.object(storage, 'run')",
            "from installer import install as installer\npatch.object(installer, 'interactive')",
            "from tools.workspace import gc\npatch.object(gc, 'merge_info')",
            "from tools.release import iso_checksums as iso\npatch.object(iso, 'validate_release')",
            "from tools.dev import qualify\npatch.object(qualify, 'newest_iso')",
            "from tools.packages import inputs as module\npatch.object(module, 'download')",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_tests(source)), 1)

    def test_constants_and_stdlib_boundaries_pass(self):
        for source in (
            "from tools.dev import try_vm\npatch.object(try_vm, 'VM_SPACE', 1)",
            "from tools.dev import try_vm\npatch.object(try_vm, 'ROOT', root)",
            "from installer import install as installer\npatch.object(installer, 'TARGET', root)",
            "from unittest.mock import patch\npatch('shutil.which')",
            "from unittest.mock import patch\npatch('sys.argv')",
            'from unittest.mock import patch\npatch.dict(os.environ, {})',
            "import sys\npatch.object(sys, 'argv', [])",
            "from pathlib import Path\npatch.object(Path, 'exists')",
            "from unittest.mock import patch\npatch('tools.dev.try_vm.subprocess.run')",
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_tests(source), [])

    def test_unresolvable_targets_pass(self):
        for source in (
            "patch('nonexistent.module.fn')",
            "x = 1\npatch.object(x, 'run')",
            "from installer import storage\nnotpatch.object(storage, 'run')",
            "from installer import storage\nnotpatch('installer.storage.run')",
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_tests(source), [])

    def test_direct_attribute_replacement_is_flagged(self):
        for source in (
            'from tools.workspace import resources\nresources.disk_guard = lambda *args: None',
            'import tools.dev.try_vm as module\nmodule.gum_input = fake',
            "from tools.workspace import resources\nsetattr(resources, 'disk_guard', fake)",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(flagged_tests(source)), 1)

    def test_constant_assignment_and_unresolvable_setattr_pass(self):
        for source in (
            'from tools.dev import try_vm\ntry_vm.VM_SPACE = 1',
            'from tools.dev import try_vm\ntry_vm.ROOT: object = root',
            'from tools.dev import try_vm\ntry_vm.ROOT: object',
            "import resources\nsetattr(resources, 'disk_guard', fake)",
            "setattr(config, 'key', value)",
            # A computed name can't be resolved statically; the rule's comment states this boundary.
            "import tools.workspace.resources as resources\nsetattr(resources, 'disk' + '_guard', fake)",
            'from tools.vm import console as vm_console\nvm_console.VM = stub',
        ):
            with self.subTest(source=source):
                self.assertEqual(flagged_tests(source), [])


class AllowCommentTests(unittest.TestCase):
    # The parser reads source lines, so fixtures splice the directive
    # together: a whole directive on one source line would itself parse as
    # an opt-out under test.
    head = '# lint-patterns: allo'

    def directive(self, rule, reason='outcome asserted below'):
        return f'{self.head}w {rule} -- {reason}'

    def test_allow_with_reason_silences_that_line(self):
        source = f'mock.assert_called_once()  {self.directive("mock_interaction_assertion")}\n'
        source += "self.assertEqual(calls, ['a'])\n"
        self.assertEqual(flagged_tests(source), [])

    def test_allow_for_another_rule_does_not_silence(self):
        source = f'mock.assert_called_once()  {self.directive("patched_repo_function")}\n'
        found = flagged_tests(source)
        self.assertEqual(len(found), 1)
        self.assertIn('mock interaction assertion', found[0])

    def test_missing_reason_is_an_error_and_keeps_the_violation(self):
        comments = (
            f'{self.head}w mock_interaction_assertion',
            f'{self.head}w mock_interaction_assertion --',
            f'{self.head}w mock_interaction_assertion --   ',
        )
        for comment in comments:
            with self.subTest(comment=comment):
                found = flagged_tests(f'mock.assert_called_once()  {comment}\n')
                self.assertEqual(len(found), 2)
                self.assertTrue(any('mock interaction assertion' in item for item in found))
                self.assertTrue(any('needs a reason' in item for item in found))

    def test_unknown_rule_is_an_error_and_keeps_the_violation(self):
        found = flagged_tests(f'mock.assert_called_once()  {self.directive("no_such_rule", "plausible")}\n')
        self.assertEqual(len(found), 2)
        self.assertTrue(any('mock interaction assertion' in item for item in found))
        self.assertTrue(any("unknown lint-patterns rule 'no_such_rule'" in item for item in found))

    def test_missing_rule_name_is_an_error(self):
        found = flagged_tests(f'mock.assert_called_once()  {self.head}w\n')
        self.assertEqual(len(found), 2)
        self.assertTrue(any('needs a rule name' in item for item in found))

    def test_tools_rule_opt_out_silences_that_line(self):
        source = 'try:\n    work()\nexcept Exception:\n    pass\n'
        self.assertEqual(len(flagged(source)), 1)
        allowed = (
            f'try:\n    work()\nexcept Exception:  {self.directive("swallowed_broad_except", "aborts")}\n    pass\n'
        )
        self.assertEqual(flagged(allowed), [])

    def test_valid_opt_out_on_a_clean_line_is_quiet(self):
        source = f"self.assertEqual(calls, ['a'])  {self.directive('mock_interaction_assertion', 'nothing')}\n"
        self.assertEqual(flagged_tests(source), [])


if __name__ == '__main__':
    unittest.main()

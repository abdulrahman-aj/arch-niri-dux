"""Regression cases for module references missed by import-only tests."""

import unittest

from tools.dev.lint_references import References


class ReferenceTests(unittest.TestCase):
    def check(self, source):
        return References(
            {
                'tools.verification.guest': 'def png_ready(): pass',
                'tools.verification.images': 'def png_dimensions(): pass',
                'tools.verification.bridge': 'from . import guest as exported',
                'tests.example': source,
            }
        ).check('tests.example')

    def test_moved_helper_fails_with_line_and_module(self):
        errors = self.check('from tools.verification import guest\nguest.png_dimensions()')
        self.assertEqual(errors, [(2, 'tools.verification.guest.png_dimensions does not exist')])

    def test_import_forms_and_reexport(self):
        for source in (
            'import tools.verification.guest as guest\nguest.png_dimensions()',
            'import tools.verification.guest\ntools.verification.guest.png_dimensions()',
            'from tools.verification.bridge import exported\nexported.png_dimensions()',
            "from unittest.mock import patch\npatch('tools.verification.guest.png_dimensions')",
            'def check():\n from tools.verification import guest\n guest.png_dimensions()',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.check(source))

    def test_existing_names_and_nonmodule_attributes_are_accepted(self):
        self.assertEqual(self.check('from tools.verification import images\nimages.png_dimensions().width'), [])

    def test_function_bindings_do_not_leak_or_shadow_other_scopes(self):
        self.assertEqual(
            self.check(
                'from tools.verification import guest\n'
                'def local(guest):\n guest.anything()\n'
                'def other():\n from tools.verification import images as guest\n guest.png_dimensions()\n'
                'guest.png_ready()'
            ),
            [],
        )

    def test_import_below_main_guard_fails_before_cli_execution(self):
        errors = self.check("if __name__ == '__main__':\n main()\nfrom tools.verification import images")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 3)
        self.assertIn('import follows __main__ guard', errors[0][1])

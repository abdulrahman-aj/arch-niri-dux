from pathlib import Path
import textwrap
import unittest

from tools.dev.lint_patterns import violations


def flagged(source):
    return list(violations(Path('tools/example.py'), textwrap.dedent(source)))


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


if __name__ == '__main__':
    unittest.main()

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify-guest.py'
spec = importlib.util.spec_from_file_location('app_defaults_probe', SCRIPT)
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class SamplePdfTests(unittest.TestCase):
    def test_generated_pdf_xref_offsets_are_valid(self):
        data = guest.sample_pdf_bytes()
        self.assertTrue(data.startswith(b'%PDF-1.4\n'))
        self.assertIn(b'Dux verification PDF works', data)
        self.assertTrue(data.rstrip().endswith(b'%%EOF'))
        lines = data.splitlines()
        xref = lines.index(b'xref')
        count = int(lines[xref + 1].split()[1])
        self.assertEqual(count, 6)
        for index in range(1, count):
            offset = int(lines[xref + 2 + index].split()[0])
            self.assertTrue(data[offset:].startswith(b'%d 0 obj' % index))


if __name__ == '__main__':
    unittest.main()

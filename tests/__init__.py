"""The test suite refuses to run as root.

The fake disk tools are safe because an unprivileged user cannot write
raw disks. Root removes that backstop, so the suite exits instead.
"""

import os
import sys
from pathlib import Path

if os.geteuid() == 0:
    sys.exit('tests refuse to run as root: raw-disk writes must stay unwritable')

# Discovery used to run without a package, so tests/ itself was importable
# and test_alongside imports test_installer top-level. Keep that working.
sys.path.append(str(Path(__file__).resolve().parent))

"""Entry point used when building a single-file binary with PyInstaller.

PyInstaller runs the script as ``__main__`` whose ``__package__`` is empty, so
package-relative imports inside ``vtdb/__main__.py`` would fail.  This script
imports the installed package normally instead.
"""

import sys

from vtdb.cli import main

if __name__ == "__main__":
    sys.exit(main())

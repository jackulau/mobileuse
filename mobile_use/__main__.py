"""`python3 -m mobile_use` — PATH-proof twin of the `mobile-use` console script.

Framework/user pip installs put console scripts in a bin dir login shells
often lack (e.g. /Library/Frameworks/Python.framework/Versions/3.X/bin), so
the module form must always work.
"""
import sys

from mobile_use.cli import main

if __name__ == "__main__":
    sys.exit(main())

"""Entry point for the packaged executable and for running from source."""

import sys

from dttwl.cli import main

if __name__ == "__main__":
    sys.exit(main())

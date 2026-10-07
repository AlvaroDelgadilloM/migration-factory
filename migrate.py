#!/usr/bin/env python3
"""camel-migration-tool CLI (Migration Factory engine). See docs/17_CLI.md or: python migrate.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
from mf.cli_tool import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())

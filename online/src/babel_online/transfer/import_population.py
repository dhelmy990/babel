#!/usr/bin/env python3
"""Run the portable population import command from an unpacked bundle."""

from __future__ import annotations

import sys

from babel_online.transfer.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["import", *sys.argv[1:]]))

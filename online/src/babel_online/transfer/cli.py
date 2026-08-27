"""Command-line contract for importing a portable population bundle."""

from __future__ import annotations

import argparse
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="import_population.py")
    commands = parser.add_subparsers(dest="command", required=True)
    import_parser = commands.add_parser(
        "import", description="Import a verified portable population bundle."
    )
    import_parser.add_argument(
        "bundle_root",
        nargs="?",
        default=".",
        help="directory containing the five-file population bundle",
    )
    import_parser.add_argument(
        "--trusted-digest",
        help="trusted SHA-256 digest of the bundle SHA256SUMS bytes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.command == "import":
        raise SystemExit("population import not implemented until import adapter")
    raise AssertionError("argparse accepted an unsupported command")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

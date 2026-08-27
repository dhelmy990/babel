"""Fail-closed command boundary for portable population transfer."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from uuid import UUID

from .database import export_population, import_population
from .parquet_bundle import verify_bundle


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babel-population-transfer")
    commands = parser.add_subparsers(dest="command", required=True)

    export_parser = commands.add_parser(
        "export", description="Export the authoritative local population read-only."
    )
    export_parser.add_argument("--database-url-env", required=True)
    export_parser.add_argument("--trial-id", required=True, type=UUID)
    export_parser.add_argument("--output-root", required=True, type=Path)
    export_parser.add_argument("--receipt", required=True, type=Path)

    verify_parser = commands.add_parser(
        "verify", description="Verify a population bundle against an independent digest."
    )
    verify_parser.add_argument("--bundle", required=True, type=Path)
    verify_parser.add_argument("--trusted-digest", required=True)

    import_parser = commands.add_parser(
        "import", description="Import a verified portable population bundle."
    )
    import_parser.add_argument("--database-url-env", required=True)
    import_parser.add_argument("--bundle", required=True, type=Path)
    import_parser.add_argument("--trusted-digest", required=True)
    import_parser.add_argument("--operator-receipt", required=True, type=Path)
    import_parser.add_argument("--fresh-trial-id", required=True, type=UUID)
    import_parser.add_argument("--fresh-run-id", required=True, type=UUID)
    import_parser.add_argument("--model-artifact-manifest", required=True, type=Path)
    import_parser.add_argument("--model-checkpoint-root", required=True, type=Path)
    import_parser.add_argument("--frozen-output-root", required=True, type=Path)
    import_parser.add_argument("--import-receipt", required=True, type=Path)
    return parser


def _write_receipt(receipt, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("export receipt destination already exists")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_mode = stat.S_IMODE(destination.parent.stat().st_mode)
    if parent_mode & 0o077:
        raise PermissionError("export receipt directory must be mode 0700 or stricter")
    document = (
        json.dumps(
            receipt.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb", closefd=True) as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
    except Exception:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.command == "import":
        environment_name = arguments.database_url_env
        if _ENVIRONMENT_NAME.fullmatch(environment_name) is None:
            parser.error("--database-url-env is not a valid environment variable name")
        database_url = os.environ.get(environment_name)
        if not database_url:
            parser.error("the named database URL environment variable is unset or empty")
        try:
            receipt = import_population(
                database_url,
                arguments.bundle,
                arguments.trusted_digest,
                arguments.operator_receipt,
                arguments.fresh_trial_id,
                arguments.fresh_run_id,
                arguments.model_artifact_manifest,
                arguments.model_checkpoint_root,
                arguments.frozen_output_root,
                arguments.import_receipt,
            )
        except Exception:
            print("population import failed", file=sys.stderr)
            return 1
        print(
            json.dumps(
                receipt.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if arguments.command == "verify":
        try:
            verified = verify_bundle(arguments.bundle, arguments.trusted_digest)
        except Exception:
            print("population bundle verification failed", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "bundleDigest": arguments.trusted_digest,
                    "rowCount": verified.manifest_contract.rowCount,
                    "verified": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if arguments.command == "export":
        environment_name = arguments.database_url_env
        if _ENVIRONMENT_NAME.fullmatch(environment_name) is None:
            parser.error("--database-url-env is not a valid environment variable name")
        database_url = os.environ.get(environment_name)
        if not database_url:
            parser.error("the named database URL environment variable is unset or empty")
        try:
            receipt = export_population(
                database_url, arguments.trial_id, arguments.output_root
            )
            _write_receipt(receipt, arguments.receipt)
        except Exception:
            print("population export failed", file=sys.stderr)
            return 1
        print(
            json.dumps(
                receipt.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    raise AssertionError("argparse accepted an unsupported command")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

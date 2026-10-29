"""First-batch command-line entry points."""

import argparse
from collections.abc import Sequence
import sys

from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.schema_export import export_schema
from psdcalc.reduction.exceptions import ReductionError


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration or export its schema; return a process code."""
    parser = argparse.ArgumentParser(prog="psdcalc-reduction")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-config", help="validate a YAML configuration")
    validate.add_argument("config")
    schema = commands.add_parser("dump-schema", help="write JSON Schema")
    schema.add_argument("path")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-config":
            config = load_config(args.config)
            print(f"VALID {config.schema_version}: {config.run.id}")
        elif args.command == "dump-schema":
            export_schema(args.path)
            print(args.path)
        else:
            parser.error(f"Unknown command: {args.command}")
    except (ReductionError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

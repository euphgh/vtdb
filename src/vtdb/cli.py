"""Command line entry point for vtdb."""

from __future__ import annotations

import sys

from .commands.args import build_parser
from .commands.common import EXIT_DB, EXIT_IO, EXIT_OK, CliError


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"vtdb: {exc}", file=sys.stderr)
        return exc.code
    except BrokenPipeError:
        return EXIT_OK
    except RuntimeError as exc:
        print(f"vtdb: {exc}", file=sys.stderr)
        return EXIT_DB
    except OSError as exc:
        print(f"vtdb: {exc}", file=sys.stderr)
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())

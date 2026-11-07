"""Implementation of the ``vtdb sql`` command."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ..output import emit_notice, render
from .common import (
    EXIT_OK,
    EXIT_USAGE,
    CliError,
    coerce_param,
    open_query_db,
    use_color,
)


def cmd_sql(args: argparse.Namespace) -> int:
    """Handle ``vtdb sql``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    text = args.query
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text or not text.strip():
        raise CliError("no query given", EXIT_USAGE)
    if not re.match(r"(?is)^\s*(select|with|explain)\b", text):
        raise CliError("only SELECT / WITH / EXPLAIN queries are allowed", EXIT_USAGE)

    params: dict[str, object] = {}
    for item in args.param:
        key, _, value = item.partition("=")
        if not key:
            raise CliError(f"invalid --param {item!r}; expected K=V", EXIT_USAGE)
        params[key] = coerce_param(value)

    conn = open_query_db(args)
    if args.explain:
        for row in conn.execute("EXPLAIN QUERY PLAN " + text, params):
            print(tuple(row))
    cursor = conn.execute(text, params)
    columns = [(desc[0], desc[0], None) for desc in cursor.description]
    rows = [dict(row) for row in cursor.fetchmany(args.limit)]
    if not rows:
        emit_notice("no rows")
    render(rows, columns, args.format, wide=args.wide, color=use_color(args))
    return EXIT_OK

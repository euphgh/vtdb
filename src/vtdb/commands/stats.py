"""Implementation of the ``vtdb stats`` command."""

from __future__ import annotations

import argparse

from .. import queries
from ..output import emit_notice, render
from .columns import CLOCK_PAIR_COLUMNS, GROUP_COLUMNS, MODULE_COLUMNS
from .common import EXIT_OK, EXIT_USAGE, CliError, fetch, open_query_db, use_color


def cmd_stats(args: argparse.Namespace) -> int:
    """Handle ``vtdb stats``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    if args.by != "module" and (args.fail_only or args.group or args.prefix):
        raise CliError(
            "--fail-only/--group/--prefix require --by module",
            EXIT_USAGE,
        )
    conn = open_query_db(args)
    if args.by == "group":
        sql, params = queries.stats_group_sql(args.sort, args.limit)
        columns = GROUP_COLUMNS
    elif args.by == "clock-pair":
        sql, params = queries.stats_clock_pair_sql(args.sort, args.limit)
        columns = CLOCK_PAIR_COLUMNS
    else:
        sql, params = queries.stats_module_sql(
            args.level, args.side, args.min_paths, args.sort, args.limit,
            fail_only=args.fail_only, group=args.group, prefix=args.prefix)
        columns = MODULE_COLUMNS
        if conn.execute("SELECT 1 FROM hier_prefix LIMIT 1").fetchone() is None:
            raise CliError(
                "no hierarchy data in this database; re-import without --no-hier",
                EXIT_USAGE,
            )
    if args.verbose:
        emit_notice(sql)
    rows = fetch(conn, sql, params, args.limit)
    if not rows:
        emit_notice("no rows")
    render(rows, columns, args.format, wide=args.wide, color=use_color(args))
    return EXIT_OK

"""Implementation of the ``vtdb top`` command."""

from __future__ import annotations

import argparse

from .. import queries
from ..output import emit_notice, render
from .columns import TOP_COLUMNS
from .common import EXIT_OK, EXIT_USAGE, CliError, fetch, open_query_db, use_color


def _filter_hint(filters: dict) -> str:
    """Return a compact description of the active ``top`` filters.

    Args:
        filters: Filter mapping passed to :func:`queries.top_sql`.

    Returns:
        Human readable conditions joined by `` AND ``.
    """
    parts: list[str] = []
    if filters.get("fail_only"):
        parts.append("slack<0")
    if filters.get("max_slack") is not None:
        parts.append(f"slack<{filters['max_slack']:g}")
    if filters.get("group"):
        operator = "=" if filters.get("group_exact") else "~"
        parts.append(f"group{operator}{filters['group']!r}")
    if filters.get("type"):
        parts.append(f"type={filters['type']}")
    if filters.get("min_levels") is not None:
        parts.append(f"levels>={filters['min_levels']}")
    if filters.get("net"):
        parts.append(f"net~{filters['net']!r}")
    if filters.get("cell"):
        parts.append(f"cell~{filters['cell']!r}")
    if filters.get("site"):
        parts.append(f"site={filters['site']!r}")
    return " AND ".join(parts)


def cmd_top(args: argparse.Namespace) -> int:
    """Handle ``vtdb top``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    conn = open_query_db(args)
    filters = {
        "fail_only": args.fail_only,
        "max_slack": args.max_slack,
        "group": args.group,
        "group_exact": args.exact,
        "type": args.path_type,
        "min_levels": args.min_levels,
        "net": args.net,
        "cell": args.cell,
        "site": args.site,
    }
    if any(filters[key] for key in ("net", "cell", "site")):
        if conn.execute("SELECT 1 FROM path_segment LIMIT 1").fetchone() is None:
            raise CliError(
                "no detail rows in this database; re-import with --keep-detail all",
                EXIT_USAGE,
            )
    sql, params = queries.top_sql(filters, args.sort, args.wide, args.limit)
    if args.verbose:
        emit_notice(sql)
    rows = fetch(conn, sql, params, args.limit)
    if not rows:
        hint = _filter_hint(filters)
        emit_notice(f"no rows ({hint})" if hint else "no rows")
    render(rows, TOP_COLUMNS, args.format, wide=args.wide, color=use_color(args))
    return EXIT_OK

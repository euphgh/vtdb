"""Implementation of the ``vtdb path`` command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import queries
from ..output import emit_notice, render
from .columns import SEGMENT_COLUMNS
from .common import (
    EXIT_DB,
    EXIT_OK,
    EXIT_USAGE,
    CliError,
    fetch,
    fmt1,
    fmt3,
    open_query_db,
    use_color,
)


def path_summary_lines(path: dict) -> list[str]:
    """Render the summary block of one path.

    Args:
        path: Row from the ``path`` table.

    Returns:
        Summary lines without trailing newlines.
    """
    return [
        f"path {path['seq']}",
        f"  slack         {path['slack']:.3f} ns   {path['slack_status']}",
        f"  type          {path['path_type']}  ({path['max_min']}, {path['corner']} corner)",
        f"  group         {path['path_group']}",
        f"  requirement   {fmt3(path['requirement_ns'])} ns",
        f"  data delay    {fmt3(path['data_delay_ns'])} ns   "
        f"logic {fmt3(path['logic_ns'])} ({fmt1(path['logic_pct'])}%)  "
        f"route {fmt3(path['route_ns'])} ({fmt1(path['route_pct'])}%)",
        f"  logic levels  {path['logic_levels']}          {path['logic_breakdown'] or ''}",
        f"  source        {path['source_pin']}   {path['src_site'] or ''}".rstrip(),
        f"  destination   {path['dest_pin']}   {path['dst_site'] or ''}".rstrip(),
        f"  clock skew    {fmt3(path['skew_ns'])} ns   "
        f"(DCD {fmt3(path['dcd_ns'])} / SCD {fmt3(path['scd_ns'])} / CPR {fmt3(path['cpr_ns'])})",
        f"  uncertainty   {fmt3(path['uncertainty_ns'])} ns",
        f"  exception     {path['timing_exception'] or '-'}",
    ]


def print_raw(conn, path: dict) -> int:
    """Print the original report lines that produced ``path``.

    Args:
        conn: Read-only database connection.
        path: Row from the ``path`` table.

    Returns:
        Process exit code.
    """
    row = conn.execute("SELECT source_file FROM report").fetchone()
    source = Path(row[0]) if row and row[0] else None
    if source is None or not source.is_file():
        emit_notice(f"raw report not available: {source}")
        return EXIT_DB
    with open(source, "r", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, 1):
            if lineno < path["line_start"]:
                continue
            if lineno > path["line_end"]:
                break
            print(line.rstrip("\n"))
    return EXIT_OK


def cmd_path(args: argparse.Namespace) -> int:
    """Handle ``vtdb path``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    conn = open_query_db(args)
    row = conn.execute("SELECT * FROM path WHERE seq = ?", (args.seq,)).fetchone()
    if row is None:
        maximum = conn.execute("SELECT MAX(seq) FROM path").fetchone()[0]
        raise CliError(f"path {args.seq}: not found (1..{maximum})", EXIT_USAGE)
    path = dict(row)
    segments: list[dict] = []
    if not args.no_segments:
        sql, params = queries.path_segments_sql(args.seq)
        segments = fetch(conn, sql, params, 10_000)

    if args.format == "json":
        print(json.dumps({"path": path, "segments": segments}, ensure_ascii=False))
        return EXIT_OK

    for line in path_summary_lines(path):
        print(line)
    if segments:
        print()
        render(segments, SEGMENT_COLUMNS, "table", wide=args.wide,
               color=use_color(args))
    if args.raw:
        return print_raw(conn, path)
    return EXIT_OK

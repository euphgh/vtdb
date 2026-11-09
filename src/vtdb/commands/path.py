"""Implementation of the ``vtdb path`` command."""

from __future__ import annotations

import argparse
import json
import textwrap
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


def _wrap_labeled(label: str, text: str, width: int = 76,
                  label_width: int = 14) -> list[str]:
    """Wrap ``text`` under a fixed-width label.

    Args:
        label: Label placed at the start of the first line.
        text: Text to wrap.
        width: Maximum total line width.
        label_width: Width of the label field after the two-space indent.

    Returns:
        Wrapped lines, each at most ``width`` characters.
    """
    prefix = f"  {label:<{label_width}}"
    chunks = textwrap.wrap(
        text, width=width - len(prefix),
        break_long_words=True, break_on_hyphens=False)
    if not chunks:
        return []
    return [prefix + chunks[0]] + [" " * len(prefix) + chunk for chunk in chunks[1:]]


def shorten_name(name: str, limit: int = 80,
                 head: int = 2, tail: int = 3) -> str:
    """Keep the first ``head`` and last ``tail`` segments of a path.

    Args:
        name: Slash separated resource name.
        limit: Maximum length of the returned name.
        head: Number of leading segments to keep.
        tail: Number of trailing segments to keep.

    Returns:
        A name no longer than ``limit`` when possible.
    """
    if limit <= 0:
        return "…"
    if not name or name == "<hidden>" or len(name) <= limit:
        return name
    parts = [part for part in name.split("/") if part]
    if len(parts) <= head + tail:
        return name[:limit - 1] + "…" if limit > 1 else "…"

    while True:
        candidate = "/".join(parts[:head]) + "/…/" + "/".join(parts[-tail:])
        if len(candidate) <= limit:
            return candidate
        if head > 1 or tail > 1:
            if head >= tail:
                head -= 1
            else:
                tail -= 1
            continue
        return candidate[:limit - 1] + "…" if limit > 1 else "…"


def _endpoint_line(label: str, name: str | None, site: str | None,
                   wide: bool) -> str:
    """Render one source or destination summary line.

    Args:
        label: ``source`` or ``destination``.
        name: Full pin name.
        site: Optional site name.
        wide: When true, keep the full pin name.

    Returns:
        Formatted summary line.
    """
    prefix = f"  {label:<14}"
    site_part = f"   {site}" if site else ""
    budget = 80 - len(prefix) - len(site_part)
    if budget < 8:
        site_part = ""
        budget = 80 - len(prefix)
    display = name or "-"
    if not wide:
        display = shorten_name(display, limit=max(1, budget))
    return f"{prefix}{display}{site_part}".rstrip()


def path_summary_lines(path: dict, *, wide: bool = False) -> list[str]:
    """Render the summary block of one path.

    Args:
        path: Row from the ``path`` table.
        wide: When true, keep full source and destination pin names.

    Returns:
        Summary lines without trailing newlines.
    """
    lines = [
        f"path {path['seq']}",
        f"  slack         {path['slack']:.3f} ns   {path['slack_status']}",
        f"  type          {path['path_type']}  ({path['max_min']}, {path['corner']} corner)",
        f"  group         {path['path_group']}",
        f"  clocks        {path['section_from_clock'] or '-'} -> "
        f"{path['section_to_clock'] or '-'}",
        f"  requirement   {fmt3(path['requirement_ns'])} ns",
        f"  data delay    {fmt3(path['data_delay_ns'])} ns   "
        f"logic {fmt3(path['logic_ns'])} ({fmt1(path['logic_pct'])}%)  "
        f"route {fmt3(path['route_ns'])} ({fmt1(path['route_pct'])}%)",
        f"  logic levels  {path['logic_levels']}          {path['logic_breakdown'] or ''}",
        _endpoint_line("source", path["source_pin"], path["src_site"], wide),
        _endpoint_line("destination", path["dest_pin"], path["dst_site"], wide),
        f"  clock skew    {fmt3(path['skew_ns'])} ns   "
        f"(DCD {fmt3(path['dcd_ns'])} / SCD {fmt3(path['scd_ns'])} / CPR {fmt3(path['cpr_ns'])})",
        f"  uncertainty   {fmt3(path['uncertainty_ns'])} ns",
        f"  exception     {path['timing_exception'] or '-'}",
    ]
    if path.get("cdc_note"):
        lines.extend(_wrap_labeled("cdc", path["cdc_note"]))
    return lines


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

    for line in path_summary_lines(path, wide=args.wide):
        print(line)
    if segments:
        print()
        render(segments, SEGMENT_COLUMNS, "table", wide=args.wide,
               color=use_color(args))
    if args.raw:
        return print_raw(conn, path)
    return EXIT_OK

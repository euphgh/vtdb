"""Command line interface for vtdb."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from . import __version__, queries
from .db import DbWriter, create_database, create_memory_database, open_database
from .output import Column, emit_notice, render
from .parser import ReportParser

EXIT_OK = 0
EXIT_CHECK = 1
EXIT_USAGE = 2
EXIT_IMPORT = 3
EXIT_DB = 4
EXIT_IO = 5

FORMATS = ("table", "json", "csv", "tsv", "md")

TOP_COLUMNS: list[Column] = [
    ("seq", "#", "int"),
    ("slack", "SLACK", "f3"),
    ("path_group", "GROUP", None),
    ("logic_levels", "LVL", "int"),
    ("route_pct", "ROUTE%", "f1"),
    ("source", "SOURCE", None),
    ("dest", "DEST", None),
    ("note", "NOTE", None),
]

GROUP_COLUMNS: list[Column] = [
    ("grp", "GROUP", None),
    ("paths", "PATHS", "int"),
    ("fail", "FAIL", "int"),
    ("wns", "WNS", "f3"),
    ("tns_sample", "TNS(SAMPLE)", "f3"),
    ("route_pct", "ROUTE%", "f1"),
]

CLOCK_PAIR_COLUMNS: list[Column] = [
    ("from_clock", "FROM", None),
    ("to_clock", "TO", None),
    ("paths", "PATHS", "int"),
    ("fail", "FAIL", "int"),
    ("wns", "WNS", "f3"),
    ("tns_sample", "TNS(SAMPLE)", "f3"),
    ("route_pct", "ROUTE%", "f1"),
]

MODULE_COLUMNS: list[Column] = [
    ("prefix", "PREFIX", None),
    ("paths", "PATHS", "int"),
    ("fail", "FAIL", "int"),
    ("wns", "WNS", "f3"),
    ("tns_sample", "TNS(SAMPLE)", "f3"),
]

SEGMENT_COLUMNS: list[Column] = [
    ("block", "BLOCK", None),
    ("seq", "SEQ", "int"),
    ("site", "SITE", None),
    ("cell_type", "CELL", None),
    ("pin_func", "ARC", None),
    ("incr_ns", "INCR", "f3"),
    ("cum_ns", "CUM", "f3"),
    ("fanout", "FO", "int"),
    ("resource", "RESOURCE", None),
]


class CliError(Exception):
    """Fatal error carrying a process exit code."""

    def __init__(self, message: str, code: int) -> None:
        """Store the message and the process exit code.

        Args:
            message: Error message printed to stderr.
            code: Process exit code.
        """
        super().__init__(message)
        self.code = code


def _add_common(parser: argparse.ArgumentParser, db_help: str) -> None:
    """Add options shared by the query subcommands.

    Args:
        parser: Subcommand parser to extend.
        db_help: Help text for ``--db``.
    """
    parser.add_argument("--db", type=Path, help=db_help)
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="only print results")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print executed SQL and timing to stderr")


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse command tree.

    Returns:
        Configured top-level parser.
    """
    parser = argparse.ArgumentParser(
        prog="vtdb",
        description="Convert Vivado report_timing_summary reports into SQLite.",
        epilog="Examples:\n"
               "  vtdb import build/post_route_timing.rpt\n"
               "  vtdb top --fail-only -n 20\n"
               "  vtdb path 42\n"
               "  vtdb stats --by module --level 4\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"vtdb {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser(
        "import", help="parse a report and create a database",
        description="Parse one report_timing_summary file into a new SQLite database.",
        epilog="Example:\n  vtdb import post_route_timing.rpt --force",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    imp.add_argument("report", type=Path)
    imp.add_argument("--db", "-o", dest="db", type=Path, default=None,
                     help="output database path (default: <report>.db)")
    imp.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    imp.add_argument("-v", "--verbose", action="store_true")
    imp.add_argument("--force", action="store_true", help="overwrite an existing database")
    imp.add_argument("--keep-detail", choices=("all", "negative", "none"), default="all",
                     help="store all detail rows, only violated paths, or none (default: all)")
    imp.add_argument("--no-hier", action="store_true",
                     help="skip hierarchy prefixes (disables stats --by module)")
    imp.add_argument("--dry-run", action="store_true", help="parse without writing a database")
    imp.add_argument("--strict", action="store_true",
                     help="return exit code 1 if consistency checks fail")
    imp.add_argument("--format", choices=("text", "json"), default="text")
    imp.set_defaults(func=cmd_import)

    top = sub.add_parser(
        "top", help="list paths by attribute or topology",
        description="Filter and rank paths. Topology filters require detail rows.",
        epilog="Examples:\n  vtdb top --fail-only -n 20\n"
               "  vtdb top --net ack_emit_bitmap_q__0 --fail-only\n"
               "  vtdb top --sort route --min-levels 20",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(top, "path to the SQLite database")
    top.add_argument("--fail-only", action="store_true", help="only slack < 0")
    top.add_argument("--max-slack", type=float, metavar="NS", help="only slack < NS")
    top.add_argument("--group", metavar="NAME", help="exact path group match")
    top.add_argument("--type", dest="path_type",
                     choices=("Setup", "Hold", "Recovery", "Removal"))
    top.add_argument("--min-levels", type=int, metavar="N")
    top.add_argument("--net", metavar="PAT", help="paths crossing a net whose leaf name matches PAT")
    top.add_argument("--cell", metavar="PAT", help="paths crossing a cell/instance matching PAT")
    top.add_argument("--site", metavar="SITE", help="paths crossing an exact site")
    top.add_argument("--sort", choices=tuple(queries.SORT_TOP), default="slack")
    top.add_argument("-n", "--limit", type=int, default=20)
    top.add_argument("--format", choices=FORMATS, default="table")
    top.add_argument("--wide", action="store_true", help="do not truncate names")
    top.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    top.set_defaults(func=cmd_top)

    path = sub.add_parser(
        "path", help="show one path with its detail rows",
        description="Show the summary and per-segment detail of one path.",
        epilog="Examples:\n  vtdb path 42\n  vtdb path 42 --no-segments\n  vtdb path 42 --raw",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(path, "path to the SQLite database")
    path.add_argument("seq", type=int)
    path.add_argument("--no-segments", action="store_true")
    path.add_argument("--raw", action="store_true",
                      help="print the original report lines of this path")
    path.add_argument("--format", choices=("table", "json"), default="table")
    path.add_argument("--wide", action="store_true")
    path.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    path.set_defaults(func=cmd_path)

    stats = sub.add_parser(
        "stats", help="aggregate paths by group, clock pair or module",
        description="Aggregate path counts and slack by group, clock pair or hierarchy prefix.",
        epilog="Examples:\n  vtdb stats --by group\n"
               "  vtdb stats --by module --level 4 --min-paths 5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(stats, "path to the SQLite database")
    stats.add_argument("--by", choices=("group", "clock-pair", "module"), default="group")
    stats.add_argument("--level", type=int, default=3, help="hierarchy depth for --by module")
    stats.add_argument("--side", choices=("source", "dest", "both"), default="source")
    stats.add_argument("--min-paths", type=int, default=1)
    stats.add_argument("--sort", choices=tuple(queries.SORT_STATS), default="wns")
    stats.add_argument("-n", "--limit", type=int, default=20)
    stats.add_argument("--format", choices=FORMATS, default="table")
    stats.add_argument("--wide", action="store_true")
    stats.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    stats.set_defaults(func=cmd_stats)

    sql = sub.add_parser(
        "sql", help="run a read-only SQL query",
        description="Run a read-only SQL query against the database.",
        epilog="Example:\n  vtdb sql \"SELECT COUNT(*) FROM path WHERE slack < 0\"",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(sql, "path to the SQLite database")
    sql.add_argument("query", nargs="?")
    sql.add_argument("--file", type=Path, help="read the query from a file")
    sql.add_argument("--param", action="append", default=[], metavar="K=V")
    sql.add_argument("--explain", action="store_true")
    sql.add_argument("-n", "--limit", type=int, default=200)
    sql.add_argument("--format", choices=FORMATS, default="table")
    sql.add_argument("--wide", action="store_true")
    sql.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    sql.set_defaults(func=cmd_sql)

    return parser


def _resolve_db(args: argparse.Namespace) -> Path:
    """Resolve the database path from options, environment or cwd.

    Args:
        args: Parsed command line arguments.

    Returns:
        Path to an existing database file.

    Raises:
        CliError: When no database is found or the choice is ambiguous.
    """
    if args.db:
        path = Path(args.db)
        if not path.is_file():
            raise CliError(f"{path}: database not found", EXIT_DB)
        return path
    env = os.environ.get("VTD_DB")
    if env:
        path = Path(env)
        if not path.is_file():
            raise CliError(f"{path}: database not found (VTD_DB)", EXIT_DB)
        return path
    candidates = sorted(Path.cwd().glob("*.db"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise CliError("no database found; run 'vtdb import <report>' or pass --db", EXIT_DB)
    names = ", ".join(path.name for path in candidates)
    raise CliError(f"multiple databases in current directory ({names}); pass --db", EXIT_DB)


def _use_color(args: argparse.Namespace) -> bool:
    """Return whether coloured output should be used.

    Args:
        args: Parsed command line arguments.

    Returns:
        ``True`` when ANSI colour should be emitted.
    """
    if getattr(args, "color", "never") == "always":
        return True
    if getattr(args, "color", "never") == "never":
        return False
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _fetch(conn, sql: str, params, limit: int) -> list[dict]:
    """Execute a query and return at most ``limit`` rows.

    Args:
        conn: Read-only database connection.
        sql: Query text.
        params: Query parameters.
        limit: Row limit; ``0`` means unlimited.

    Returns:
        Rows as dictionaries.
    """
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall() if limit <= 0 else cursor.fetchmany(limit)
    return [dict(row) for row in rows]


def cmd_import(args: argparse.Namespace) -> int:
    """Handle ``vtdb import``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    report = Path(args.report)
    if not report.is_file():
        raise CliError(f"{report}: no such file", EXIT_IMPORT)
    output = Path(args.db) if args.db else report.with_suffix(".db")
    if not args.dry_run and output.exists() and not args.force:
        raise CliError(f"{output} exists; use --force to overwrite", EXIT_IMPORT)

    started = time.time()
    if args.dry_run:
        conn = create_memory_database()
    else:
        conn = create_database(output)
    writer = DbWriter(conn)
    parser = ReportParser(report, writer, keep_detail=args.keep_detail,
                          hier=not args.no_hier)
    result = parser.run()
    writer.close()

    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall() if not args.dry_run else []
    violated = conn.execute("SELECT COUNT(*) FROM path WHERE slack < 0").fetchone()[0]
    elapsed = time.time() - started
    size = output.stat().st_size if output.exists() and not args.dry_run else 0
    checks_ok = not result.issues and not foreign_keys

    if args.format == "json":
        print(json.dumps({
            "report": str(report),
            "database": None if args.dry_run else str(output),
            "design": result.meta.get("Design"),
            "state": result.meta.get("Design State"),
            "paths": result.counts.get("paths", 0),
            "violated": violated,
            "segments": result.counts.get("segments", 0),
            "pw_checks": result.counts.get("pw", 0),
            "checks": "ok" if checks_ok else "failed",
            "issues": [{"line": line, "severity": severity, "code": code, "message": message}
                       for line, severity, code, message in result.issues],
            "elapsed_s": round(elapsed, 2),
            "size_bytes": size,
        }, ensure_ascii=False))
    elif not args.quiet:
        target = f"{report} -> {output}" if not args.dry_run else f"{report} (dry run)"
        print(f"imported {target}")
        print(f"  design     {result.meta.get('Design')} ({result.meta.get('Design State')})")
        print(f"  paths      {result.counts.get('paths', 0)} ({violated} violated)")
        print(f"  segments   {result.counts.get('segments', 0)}")
        print(f"  pw checks  {result.counts.get('pw', 0)}")
        if result.issues or foreign_keys:
            print(f"  checks     FAILED ({len(result.issues) + len(foreign_keys)} issues)")
            for line, severity, code, message in result.issues[:10]:
                print(f"    line {line}  {code}  {message}")
            for row in foreign_keys[:10]:
                print(f"    foreign key violation: {row}")
        else:
            print("  checks     ok")
        print(f"  elapsed    {elapsed:.1f}s")
        if not args.dry_run:
            print(f"  size       {size / 1e6:.1f} MB")

    if args.strict and not checks_ok:
        return EXIT_CHECK
    return EXIT_OK


def cmd_top(args: argparse.Namespace) -> int:
    """Handle ``vtdb top``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    conn = open_database(_resolve_db(args))
    filters = {
        "fail_only": args.fail_only,
        "max_slack": args.max_slack,
        "group": args.group,
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
    rows = _fetch(conn, sql, params, args.limit)
    if not rows:
        emit_notice("no rows")
    render(rows, TOP_COLUMNS, args.format, wide=args.wide, color=_use_color(args))
    return EXIT_OK


def cmd_path(args: argparse.Namespace) -> int:
    """Handle ``vtdb path``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    conn = open_database(_resolve_db(args))
    row = conn.execute("SELECT * FROM path WHERE seq = ?", (args.seq,)).fetchone()
    if row is None:
        maximum = conn.execute("SELECT MAX(seq) FROM path").fetchone()[0]
        raise CliError(f"path {args.seq}: not found (1..{maximum})", EXIT_USAGE)
    path = dict(row)
    segments: list[dict] = []
    if not args.no_segments:
        sql, params = queries.path_segments_sql(args.seq)
        segments = _fetch(conn, sql, params, 10_000)

    if args.format == "json":
        print(json.dumps({"path": path, "segments": segments}, ensure_ascii=False))
        return EXIT_OK

    print(f"path {path['seq']}")
    print(f"  slack         {path['slack']:.3f} ns   {path['slack_status']}")
    print(f"  type          {path['path_type']}  ({path['max_min']}, {path['corner']} corner)")
    print(f"  group         {path['path_group']}")
    print(f"  requirement   {_fmt(path['requirement_ns'])} ns")
    print(f"  data delay    {_fmt(path['data_delay_ns'])} ns   "
          f"logic {_fmt(path['logic_ns'])} ({_fmt1(path['logic_pct'])}%)  "
          f"route {_fmt(path['route_ns'])} ({_fmt1(path['route_pct'])}%)")
    print(f"  logic levels  {path['logic_levels']}          {path['logic_breakdown'] or ''}")
    print(f"  source        {path['source_pin']}   {path['src_site'] or ''}".rstrip())
    print(f"  destination   {path['dest_pin']}   {path['dst_site'] or ''}".rstrip())
    print(f"  clock skew    {_fmt(path['skew_ns'])} ns   "
          f"(DCD {_fmt(path['dcd_ns'])} / SCD {_fmt(path['scd_ns'])} / CPR {_fmt(path['cpr_ns'])})")
    print(f"  uncertainty   {_fmt(path['uncertainty_ns'])} ns")
    print(f"  exception     {path['timing_exception'] or '-'}")
    if segments:
        print()
        render(segments, SEGMENT_COLUMNS, "table", wide=args.wide,
               color=_use_color(args))
    if args.raw:
        return _print_raw(conn, path)
    return EXIT_OK


def _print_raw(conn, path: dict) -> int:
    """Print the original report lines that produced ``path``."""
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


def cmd_stats(args: argparse.Namespace) -> int:
    """Handle ``vtdb stats``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    conn = open_database(_resolve_db(args))
    if args.by == "group":
        sql, params = queries.stats_group_sql(args.sort, args.limit)
        columns = GROUP_COLUMNS
    elif args.by == "clock-pair":
        sql, params = queries.stats_clock_pair_sql(args.sort, args.limit)
        columns = CLOCK_PAIR_COLUMNS
    else:
        sql, params = queries.stats_module_sql(args.level, args.side, args.min_paths,
                                               args.sort, args.limit)
        columns = MODULE_COLUMNS
        if conn.execute("SELECT 1 FROM hier_prefix LIMIT 1").fetchone() is None:
            raise CliError(
                "no hierarchy data in this database; re-import without --no-hier",
                EXIT_USAGE,
            )
    if args.verbose:
        emit_notice(sql)
    rows = _fetch(conn, sql, params, args.limit)
    if not rows:
        emit_notice("no rows")
    render(rows, columns, args.format, wide=args.wide, color=_use_color(args))
    return EXIT_OK


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
        params[key] = _coerce(value)

    conn = open_database(_resolve_db(args))
    if args.explain:
        for row in conn.execute("EXPLAIN QUERY PLAN " + text, params):
            print(tuple(row))
    cursor = conn.execute(text, params)
    columns = [(desc[0], desc[0], None) for desc in cursor.description]
    rows = [dict(row) for row in cursor.fetchmany(args.limit)]
    if not rows:
        emit_notice("no rows")
    render(rows, columns, args.format, wide=args.wide, color=_use_color(args))
    return EXIT_OK


def _coerce(value: str):
    """Convert a ``--param`` value to int, float or str.

    Args:
        value: Raw parameter text.

    Returns:
        Converted value.
    """
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _fmt(value) -> str:
    """Format a float with three decimals, or ``-`` when missing.

    Args:
        value: Value to format.

    Returns:
        Formatted text.
    """
    return "-" if value is None else f"{value:.3f}"


def _fmt1(value) -> str:
    """Format a float with one decimal, or ``-`` when missing.

    Args:
        value: Value to format.

    Returns:
        Formatted text.
    """
    return "-" if value is None else f"{value:.1f}"


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

"""Argument parser construction for the vtdb CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import __version__, queries
from .common import FORMATS
from .import_cmd import cmd_import
from .path import cmd_path
from .sql import cmd_sql
from .stats import cmd_stats
from .top import cmd_top


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
    top.add_argument("--group", metavar="NAME",
                     help="path group name (substring; use --exact for exact match)")
    top.add_argument("--exact", action="store_true",
                     help="require an exact --group match")
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
    path.add_argument("--wide", action="store_true",
                       help="show full endpoint names and detail columns")
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
    stats.add_argument("--fail-only", action="store_true",
                       help="only count slack < 0 (module)")
    stats.add_argument("--group", metavar="TEXT",
                       help="only paths whose group contains TEXT (module)")
    stats.add_argument("--prefix", metavar="TEXT",
                       help="only prefixes containing TEXT (module)")
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

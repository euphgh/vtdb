"""Shared helpers for the vtdb query commands."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from ..db import open_database

EXIT_OK = 0
EXIT_CHECK = 1
EXIT_USAGE = 2
EXIT_IMPORT = 3
EXIT_DB = 4
EXIT_IO = 5

FORMATS = ("table", "json", "csv", "tsv", "md")


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


def resolve_db(args: argparse.Namespace) -> Path:
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


def open_query_db(args: argparse.Namespace) -> sqlite3.Connection:
    """Open the database selected by the common query options.

    Args:
        args: Parsed command line arguments.

    Returns:
        Read-only database connection.

    Raises:
        CliError: When the database cannot be resolved.
    """
    return open_database(resolve_db(args))


def use_color(args: argparse.Namespace) -> bool:
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


def fetch(conn: sqlite3.Connection, sql: str, params, limit: int) -> list[dict]:
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


def coerce_param(value: str):
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


def fmt3(value) -> str:
    """Format a float with three decimals, or ``-`` when missing.

    Args:
        value: Value to format.

    Returns:
        Formatted text.
    """
    return "-" if value is None else f"{value:.3f}"


def fmt1(value) -> str:
    """Format a float with one decimal, or ``-`` when missing.

    Args:
        value: Value to format.

    Returns:
        Formatted text.
    """
    return "-" if value is None else f"{value:.1f}"

"""Implementation of the ``vtdb import`` command."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..db import DbWriter, create_database, create_memory_database
from ..parser import ParseResult, ReportParser
from .common import EXIT_CHECK, EXIT_IMPORT, EXIT_OK, CliError


@dataclass
class ImportOutcome:
    """Result of parsing one report and writing its database.

    Attributes:
        report: Report file that was parsed.
        output: Database path that was requested.
        database: Written database, or ``None`` for ``--dry-run``.
        result: Parser result with metadata, counts and issues.
        violated: Number of paths with negative slack.
        foreign_keys: Foreign key violations found after the import.
        elapsed: Wall clock time of the import in seconds.
        size: Database size in bytes, or ``0`` for ``--dry-run``.
    """

    report: Path
    output: Path
    database: Path | None
    result: ParseResult
    violated: int
    foreign_keys: list
    elapsed: float
    size: int

    @property
    def checks_ok(self) -> bool:
        """Return whether parser issues and foreign keys are both clean.

        Returns:
            ``True`` when no issue was reported.
        """
        return not self.result.issues and not self.foreign_keys


def run_import(args: argparse.Namespace) -> ImportOutcome:
    """Parse the report selected by ``args`` and write its database.

    Args:
        args: Parsed command line arguments.

    Returns:
        Import outcome with counts, issues and output paths.

    Raises:
        CliError: When the report is missing or the output exists.
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
    return ImportOutcome(
        report=report,
        output=output,
        database=None if args.dry_run else output,
        result=result,
        violated=violated,
        foreign_keys=foreign_keys,
        elapsed=elapsed,
        size=size,
    )


def render_import(outcome: ImportOutcome, args: argparse.Namespace) -> None:
    """Print the import summary in text or JSON form.

    Args:
        outcome: Result of :func:`run_import`.
        args: Parsed command line arguments.
    """
    result = outcome.result
    if args.format == "json":
        print(json.dumps({
            "report": str(outcome.report),
            "database": None if outcome.database is None else str(outcome.database),
            "design": result.meta.get("Design"),
            "state": result.meta.get("Design State"),
            "paths": result.counts.get("paths", 0),
            "violated": outcome.violated,
            "segments": result.counts.get("segments", 0),
            "pw_checks": result.counts.get("pw", 0),
            "checks": "ok" if outcome.checks_ok else "failed",
            "issues": [{"line": line, "severity": severity, "code": code, "message": message}
                       for line, severity, code, message in result.issues],
            "elapsed_s": round(outcome.elapsed, 2),
            "size_bytes": outcome.size,
        }, ensure_ascii=False))
        return

    if args.quiet:
        return
    target = (f"{outcome.report} -> {outcome.output}" if outcome.database is not None
              else f"{outcome.report} (dry run)")
    print(f"imported {target}")
    print(f"  design     {result.meta.get('Design')} ({result.meta.get('Design State')})")
    print(f"  paths      {result.counts.get('paths', 0)} ({outcome.violated} violated)")
    print(f"  segments   {result.counts.get('segments', 0)}")
    print(f"  pw checks  {result.counts.get('pw', 0)}")
    if result.issues or outcome.foreign_keys:
        print(f"  checks     FAILED ({len(result.issues) + len(outcome.foreign_keys)} issues)")
        for line, severity, code, message in result.issues[:10]:
            print(f"    line {line}  {code}  {message}")
        for row in outcome.foreign_keys[:10]:
            print(f"    foreign key violation: {row}")
    else:
        print("  checks     ok")
    print(f"  elapsed    {outcome.elapsed:.1f}s")
    if outcome.database is not None:
        print(f"  size       {outcome.size / 1e6:.1f} MB")


def cmd_import(args: argparse.Namespace) -> int:
    """Handle ``vtdb import``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    outcome = run_import(args)
    render_import(outcome, args)
    if args.strict and not outcome.checks_ok:
        return EXIT_CHECK
    return EXIT_OK

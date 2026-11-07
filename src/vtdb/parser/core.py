"""Streaming parser for Vivado ``report_timing_summary`` text reports.

The parser is a single pass state machine over the report file.  It never holds
the whole report in memory: paths and detail rows are handed to a
:class:`vtdb.db.DbWriter`, which batches them into SQLite.

Design notes that matter for correctness:

* A path block has three detail sections (launch clock, data, capture clock)
  delimited by two-column separators.  Rows are tagged with the block they
  belong to.
* ``arrival time`` is printed negated by Vivado.  It is flipped on import so
  that ``required - arrival == slack``.
* Long ``Location + Delay type`` cells push the numeric columns to the next
  line; the parser keeps a ``pending`` pair and merges it forward.
* ``<hidden>`` names are legal and are never used as identity keys.
"""

from __future__ import annotations

from pathlib import Path

from ..db import DbWriter, sha256_file
from .details import DetailsParser
from .lexer import RE_META
from .model import ParseResult
from .report import write_report
from .sections import TopSections, detect_section


class ReportParser:
    """Parse one ``report_timing_summary`` file into a :class:`DbWriter`."""

    def __init__(self, report_path: Path, writer: DbWriter,
                 keep_detail: str = "all", hier: bool = True) -> None:
        """Initialise the parser state.

        Args:
            report_path: Report file to parse.
            writer: Destination for parsed rows.
            keep_detail: ``all``, ``negative`` or ``none``.
            hier: Whether to emit hierarchy prefixes.
        """
        self.report_path = Path(report_path)
        self.writer = writer
        self.keep_detail = keep_detail
        self.hier = hier
        self.result = ParseResult()

        self.section = "top"
        self.sections = TopSections(writer)
        self.details = DetailsParser(writer, keep_detail, hier, self.result.issues)
        self._sha = sha256_file(self.report_path)

    def run(self) -> ParseResult:
        """Parse the whole report and emit report-level rows.

        Returns:
            Parse result with counts, metadata and issues.
        """
        with open(self.report_path, "r", encoding="utf-8", errors="replace") as handle:
            for lineno, raw in enumerate(handle, 1):
                self._line(lineno, raw.rstrip("\n"))
        self.details.finish()
        self.sections.finish()

        write_report(
            self.writer,
            self.report_path,
            self._sha,
            self.sections.meta,
            self.sections.design,
            self.sections.constraints_met,
            self.writer.counts,
        )
        self.result.meta = self.sections.meta
        self.result.design = self.sections.design
        self.result.counts = dict(self.writer.counts)
        return self.result

    def _line(self, lineno: int, line: str) -> None:
        """Dispatch one non-empty line to the active handler.

        Args:
            lineno: 1-based line number in the report.
            line: Line without the trailing newline.
        """
        if not line.strip():
            return
        stripped = line.strip()

        if line.startswith("| ") and RE_META.match(stripped):
            self.sections.add_meta(stripped)
            return

        section = detect_section(line)
        if section is not None:
            self.section = section
            return

        if self.section == "details":
            self.details.feed(lineno, line)
            return
        self.section = self.sections.feed(self.section, line)

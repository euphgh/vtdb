"""Top-level report sections other than Timing Details."""

from __future__ import annotations

import re

from ..db import DbWriter
from .lexer import (
    RE_CHECK,
    RE_CLOCK_ROW,
    RE_DESIGN_ROW,
    RE_META,
    _fnum,
    _inum,
)
from .model import CheckTimingRow

SECTION_PREFIXES = (
    ("Timing Summary Report", "top"),
    ("| Timer Settings", "timer"),
    ("check_timing report", "check"),
    ("| Design Timing Summary", "design"),
    ("| Clock Summary", "clock"),
    ("| Intra Clock Table", "intra"),
    ("| Inter Clock Table", "inter"),
    ("| Other Path Groups Table", "other"),
    ("| Timing Details", "details"),
)


def detect_section(line: str) -> str | None:
    """Return the section started by ``line``, if any.

    Args:
        line: Raw report line.

    Returns:
        Section name or ``None`` when the line is not a section header.
    """
    for prefix, section in SECTION_PREFIXES:
        if line.startswith(prefix):
            return section
    return None


class TopSections:
    """Accumulate metadata, timer settings, tables and check_timing rows."""

    def __init__(self, writer: DbWriter) -> None:
        """Initialise the section handler.

        Args:
            writer: Destination for report-level rows.
        """
        self.writer = writer
        self.meta: dict[str, str] = {}
        self.design: dict[str, float | int | None] = {}
        self.constraints_met: int | None = None
        self.checks: dict[int, CheckTimingRow] = {}
        self.clocks: list[tuple[str, str | None, int]] = []
        self.current_check: int | None = None
        self._clock_values: dict[str, tuple[str | None, float | None, float | None]] = {}

    def add_meta(self, line: str) -> None:
        """Record one header metadata line.

        Args:
            line: Stripped line matching ``RE_META``.
        """
        match = RE_META.match(line)
        if match:
            self.meta[match.group(1).strip()] = match.group(2)

    def feed(self, section: str, line: str) -> str:
        """Handle one line of a non-details section.

        Args:
            section: Current section name.
            line: Raw report line.

        Returns:
            Possibly updated section name.
        """
        if section == "timer":
            self._timer_line(line)
        elif section in ("design", "after_design"):
            return self._design_line(section, line)
        elif section == "clock":
            self._clock_line(line)
        elif section in ("intra", "inter", "other"):
            self._summary_line(section, line)
        elif section == "check":
            self._check_line(line)
        return section

    def finish(self) -> None:
        """Write the accumulated check_timing and clock rows."""
        for seq in sorted(self.checks):
            row = self.checks[seq]
            self.writer.add_check_timing(
                row.seq, row.category, row.count, row.severity, row.detail)
        for name, parent, indent in self.clocks:
            self.writer.add_clock(
                name, parent, indent,
                *self._clock_values.get(name, (None, None, None)))

    def _timer_line(self, line: str) -> None:
        """Handle a line inside the Timer Settings section.

        Args:
            line: Raw report line.
        """
        match = re.match(r"^  (\S.*?)\s+:\s+(.*)$", line)
        if match:
            self.writer.add_timer_setting(match.group(1).strip(), match.group(2).strip())

    def _design_line(self, section: str, line: str) -> str:
        """Handle a line inside the Design Timing Summary section.

        Args:
            section: Current section name.
            line: Raw report line.

        Returns:
            ``after_design`` after the summary row, otherwise ``section``.
        """
        if line.startswith("Timing constraints are not met"):
            self.constraints_met = 0
            return section
        if line.startswith("All user specified timing constraints are met"):
            self.constraints_met = 1
            return section
        match = RE_DESIGN_ROW.match(line)
        if not match:
            return section
        self.design = {
            "wns": _fnum(match.group(1)),
            "tns": _fnum(match.group(2)),
            "tns_failing": int(match.group(3)),
            "tns_total": int(match.group(4)),
            "wpws": _fnum(match.group(5)),
            "tpws": _fnum(match.group(6)),
            "tpws_failing": int(match.group(7)),
            "tpws_total": int(match.group(8)),
        }
        self.writer.add_summary({"kind": "design", **self.design})
        return "after_design"

    def _clock_line(self, line: str) -> None:
        """Handle one row of the Clock Summary table.

        Args:
            line: Raw report line.
        """
        stripped = line.strip()
        if stripped.startswith("Clock ") or stripped.startswith("-----"):
            return
        match = RE_CLOCK_ROW.match(stripped)
        if not match:
            return
        name = match.group(1)
        indent = len(line) - len(line.lstrip())
        parent = None
        for other, _, other_indent in reversed(self.clocks):
            if other_indent < indent:
                parent = other
                break
        self.clocks.append((name, parent, indent))
        self._clock_values[name] = (
            match.group(2), _fnum(match.group(3)), _fnum(match.group(4)))

    def _summary_line(self, section: str, line: str) -> None:
        """Handle one row of the Intra/Inter/Other summary tables.

        Args:
            section: Table section name.
            line: Raw report line.
        """
        if line.startswith("-"):
            return
        parts = re.split(r"\s{2,}", line.strip())
        kind = section
        if kind == "intra" and len(parts) >= 9 and _fnum(parts[1]) is not None:
            self.writer.add_summary({
                "kind": "intra", "path_group": parts[0], "wns": _fnum(parts[1]),
                "tns": _fnum(parts[2]), "failing_endpoints": _inum(parts[3]),
                "total_endpoints": _inum(parts[4]), "wpws": _fnum(parts[5]),
                "tpws": _fnum(parts[6]), "tpws_failing": _inum(parts[7]),
                "tpws_total": _inum(parts[8]),
            })
        elif kind == "inter" and len(parts) >= 6 and _fnum(parts[2]) is not None:
            self.writer.add_summary({
                "kind": "inter", "from_clock": parts[0], "to_clock": parts[1],
                "wns": _fnum(parts[2]), "tns": _fnum(parts[3]),
                "failing_endpoints": _inum(parts[4]), "total_endpoints": _inum(parts[5]),
            })
        elif kind == "other" and len(parts) >= 7 and _fnum(parts[3]) is not None:
            self.writer.add_summary({
                "kind": "other", "path_group": parts[0], "from_clock": parts[1],
                "to_clock": parts[2], "wns": _fnum(parts[3]), "tns": _fnum(parts[4]),
                "failing_endpoints": _inum(parts[5]), "total_endpoints": _inum(parts[6]),
            })

    def _check_line(self, line: str) -> None:
        """Handle a ``check_timing`` header or detail line.

        Args:
            line: Raw report line.
        """
        match = RE_CHECK.match(line)
        if match:
            seq = int(match.group(1))
            self.current_check = seq
            self.checks[seq] = CheckTimingRow(
                seq, match.group(2), int(match.group(3)))
            return
        if not line.startswith(" ") or self.current_check is None:
            return
        row = self.checks[self.current_check]
        severity = re.search(r"\((HIGH|MEDIUM|LOW)\)", line)
        if severity:
            row.severity = severity.group(1)
        row.detail = (row.detail + " " + line.strip()).strip()

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

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .db import DbWriter, sha256_file

RE_META = re.compile(r"^\| ([A-Za-z][A-Za-z ]*?)\s*:\s+(.*?)\s*$")
RE_SLACK = re.compile(r"^Slack \((MET|VIOLATED)\)\s*:\s*(-?[\d.]+|NA|inf)ns")
RE_FIELD = re.compile(r"^  ([A-Za-z][A-Za-z0-9 ()/^.,+-]*?)\s*:\s+(.*?)\s*$")
RE_SUBF = re.compile(r"^    (\S.*?)\s*:\s+(-?[\d.]+)ns")
RE_PAIR = re.compile(
    r"^(Setup|Hold|PW)\s*:\s*(\S+)\s+Failing Endpoints,\s+Worst Slack\s+(\S+?)\s*,"
    r"\s+Total Violation\s+(\S+)"
)
RE_CHECK = re.compile(r"^(\d+)\. checking (\w+) \((\d+)\)")
RE_NUM2 = re.compile(r"(-?\d+\.\d{3})\s+(-?\d+\.\d{3})")
RE_DATA_DELAY = re.compile(
    r"(-?[\d.]+)ns\s+\(logic\s+(-?[\d.]+)ns\s+\(([\d.]+)%\)\s+route\s+(-?[\d.]+)ns\s+\(([\d.]+)%\)\)"
)
RE_REQUIREMENT = re.compile(r"^(-?[\d.]+|inf)ns\s*(?:\((.*)\))?")
RE_LEVELS = re.compile(r"^(\d+)\s+\((.*)\)$")
RE_LEVEL_ITEM = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=(\d+)")
RE_CLKNET = re.compile(r"^(-?[\d.]+)ns\s+\(routing\s+(-?[\d.]+)ns,\s+distribution\s+(-?[\d.]+)ns\)")
RE_CELL_CLOCK = re.compile(r"edge-triggered (?:cell|pin) (\S+) clocked by (\S+)")
RE_SEP2 = re.compile(r"^\s*-{10,}\s{2,}-{5,}\s*$")
RE_SEP = re.compile(r"^\s*-{10,}\s*$")
RE_CLOCK_ROW = re.compile(r"^(\S.*?)\s{2,}(\{[^}]+\})\s{2,}(-?[\d.]+)\s{2,}(-?[\d.]+)\s*$")
RE_DESIGN_ROW = re.compile(
    r"^\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(\d+)\s+(\d+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(\d+)\s+(\d+)"
)
RE_CMD_OPTION = r"-{option}\s+(?:(\"[^\"]*\")|(\S+))"

BLOCK_NAMES = ("launch_clock", "data", "capture_clock")
LOC_START = 4
TYPE_START = 25


def split_prefix(line: str) -> tuple[str | None, int]:
    """Split the ``Location`` and ``Delay type`` columns of a detail row.

    Vivado prints the location starting at column 4 and the delay type at
    column 25.  A location longer than the column pushes the delay type to the
    right, so the split is detected instead of hard coded.

    Args:
        line: Raw report line.

    Returns:
        ``(location, delay_type_start)``.
    """
    match = re.match(r"^ {4}(\S+)\s{2,}", line)
    if match and match.end() > TYPE_START:
        return match.group(1), match.end()
    if len(line) <= TYPE_START:
        return None, TYPE_START
    return line[LOC_START:TYPE_START].strip() or None, TYPE_START

SUBFIELD_MAP = {
    "Destination Clock Delay (DCD)": "dcd_ns",
    "Source Clock Delay (SCD)": "scd_ns",
    "Clock Pessimism Removal (CPR)": "cpr_ns",
    "Total System Jitter (TSJ)": "tsj_ns",
    "Total Input Jitter (TIJ)": "tij_ns",
    "Discrete Jitter (DJ)": "dj_ns",
    "Phase Error (PE)": "pe_ns",
}


def _fnum(text: str | None) -> float | None:
    """Convert text to ``float`` when possible.

    Args:
        text: Numeric text or ``None``.

    Returns:
        Parsed float or ``None``.
    """
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _inum(text: str | None) -> int | None:
    """Convert decimal text to ``int`` when possible.

    Args:
        text: Decimal text or ``None``.

    Returns:
        Parsed integer or ``None``.
    """
    if text is None or not text.isdigit():
        return None
    return int(text)


def split_delay_type(text: str | None) -> tuple[bool, str | None, str | None, int | None]:
    """Split a ``Delay type`` cell into (is_net, cell_type, pin_func, fanout)."""
    if not text:
        return False, None, None, None
    if text.startswith("net"):
        match = re.search(r"fo=(\d+)", text)
        return True, None, text, int(match.group(1)) if match else None
    match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*\((.*)\)$", text)
    if match:
        return False, match.group(1), match.group(2), None
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", text):
        return False, text, None, None
    return False, None, text, None


@dataclass
class ParseResult:
    """Summary of one parsed report."""

    meta: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    issues: list[tuple[int | None, str, str, str]] = field(default_factory=list)
    design: dict[str, float | int | None] = field(default_factory=dict)


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
        self.mode: str | None = None
        self.pair: dict[str, object] = {}
        self.section_seq = 0
        self.path: dict[str, object] | None = None
        self.path_stats: dict[str, object] = {}
        self.block_index = 0
        self.seg_seq = 0
        self.pending: tuple[str | None, str | None] | None = None
        self.last_label: str | None = None
        self.pw_header = False
        self.pw_clock: str | None = None
        self.pw_id = 0
        self.checks: dict[int, list[object]] = {}
        self.clocks: list[tuple[str, str | None, int]] = []
        self.meta: dict[str, str] = {}
        self.design: dict[str, float | int | None] = {}
        self.constraints_met: int | None = None
        self.current_check: int | None = None
        self._clock_values: dict[str, tuple[str | None, float | None, float | None]] = {}
        self._sha = sha256_file(self.report_path)

    # -- public -------------------------------------------------------------
    def run(self) -> ParseResult:
        """Parse the whole report and emit report-level rows.

        Returns:
            Parse result with counts, metadata and issues.
        """
        with open(self.report_path, "r", encoding="utf-8", errors="replace") as handle:
            for lineno, raw in enumerate(handle, 1):
                self._line(lineno, raw.rstrip("\n"))
        self._flush_pair()

        for seq in sorted(self.checks):
            row = self.checks[seq]
            self.writer.add_check_timing(seq, row[1], row[2], row[3], row[4])
        for name, parent, indent in self.clocks:
            self.writer.add_clock(name, parent, indent, *self._clock_values.get(name, (None, None, None)))

        self._write_report()
        self.result.meta = self.meta
        self.result.design = self.design
        self.result.counts = dict(self.writer.counts)
        return self.result

    # -- line dispatch ------------------------------------------------------
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
            match = RE_META.match(stripped)
            self.meta[match.group(1).strip()] = match.group(2)
            return

        if line.startswith("Timing Summary Report"):
            self.section = "top"
            return
        if line.startswith("| Timer Settings"):
            self.section = "timer"
            return
        if line.startswith("check_timing report"):
            self.section = "check"
            return
        if line.startswith("| Design Timing Summary"):
            self.section = "design"
            return
        if line.startswith("| Clock Summary"):
            self.section = "clock"
            return
        if line.startswith("| Intra Clock Table"):
            self.section = "intra"
            return
        if line.startswith("| Inter Clock Table"):
            self.section = "inter"
            return
        if line.startswith("| Other Path Groups Table"):
            self.section = "other"
            return
        if line.startswith("| Timing Details"):
            self.section = "details"
            return

        if self.section == "timer":
            self._timer_line(line)
            return
        if self.section in ("design", "after_design"):
            self._design_line(line)
            return
        if self.section == "clock":
            self._clock_line(line)
            return
        if self.section in ("intra", "inter", "other"):
            self._summary_line(line)
            return
        if self.section == "check":
            self._check_line(line)
            return
        if self.section == "details":
            self._details_line(lineno, line)

    # -- sections -----------------------------------------------------------
    def _timer_line(self, line: str) -> None:
        """Handle a line inside the Timer Settings section.

        Args:
            line: Raw report line.
        """
        match = re.match(r"^  (\S.*?)\s+:\s+(.*)$", line)
        if match:
            self.writer.add_timer_setting(match.group(1).strip(), match.group(2).strip())

    def _design_line(self, line: str) -> None:
        """Handle a line inside the Design Timing Summary section.

        Args:
            line: Raw report line.
        """
        if line.startswith("Timing constraints are not met"):
            self.constraints_met = 0
            return
        if line.startswith("All user specified timing constraints are met"):
            self.constraints_met = 1
            return
        match = RE_DESIGN_ROW.match(line)
        if not match:
            return
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
        self.section = "after_design"

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
        self._clock_values[name] = (match.group(2), _fnum(match.group(3)), _fnum(match.group(4)))

    def _summary_line(self, line: str) -> None:
        """Handle one row of the Intra/Inter/Other summary tables.

        Args:
            line: Raw report line.
        """
        if line.startswith("-"):
            return
        parts = re.split(r"\s{2,}", line.strip())
        kind = self.section
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
            self.checks[seq] = [seq, match.group(2), int(match.group(3)), None, ""]
            return
        if not line.startswith(" ") or self.current_check is None:
            return
        row = self.checks[self.current_check]
        severity = re.search(r"\((HIGH|MEDIUM|LOW)\)", line)
        if severity:
            row[3] = severity.group(1)
        row[4] = (row[4] + " " + line.strip()).strip()

    # -- timing details -----------------------------------------------------
    def _details_line(self, lineno: int, line: str) -> None:
        """Handle a line inside the Timing Details section.

        Args:
            lineno: 1-based line number in the report.
            line: Raw report line.
        """
        if line.startswith("From Clock:"):
            self._flush_pair()
            self.section_seq += 1
            self.pair = {
                "section_seq": self.section_seq,
                "from_clock": line.split(":", 1)[1].strip(),
                "to_clock": None,
            }
            self.mode = None
            self.path = None
            return
        if line.startswith("  To Clock:"):
            self.pair["to_clock"] = line.split(":", 1)[1].strip()
            return

        match = RE_PAIR.match(line)
        if match:
            prefix = {"Setup": "setup", "Hold": "hold", "PW": "pw"}[match.group(1)]
            for key, value in (("failing", match.group(2)), ("wns", match.group(3)),
                               ("tns", match.group(4))):
                self.pair[f"{prefix}_{key}"] = None if value == "NA" else _fnum(value)
            self.pair[f"{prefix}_failing"] = _inum(match.group(2))
            return

        stripped = line.strip()
        if stripped in ("Max Delay Paths", "Min Delay Paths"):
            self.mode = "path_head"
            self.block_index = 0
            return
        if stripped == "Pulse Width Checks":
            self.mode = "pw_head"
            self.pw_header = False
            return
        if self.mode in ("path_head", "path_table"):
            self._path_line(lineno, line)
        elif self.mode == "pw_head":
            self._pw_line(line)

    def _path_line(self, lineno: int, line: str) -> None:
        """Handle a line of a path block.

        Args:
            lineno: 1-based line number in the report.
            line: Raw report line.
        """
        if line.startswith("Slack ("):
            match = RE_SLACK.match(line)
            if not match:
                return
            slack = _fnum(match.group(2))
            self.path = {
                "seq": self.path_stats.get("next_seq", 1),
                "line_start": lineno,
                "line_end": None,
                "section_seq": self.pair.get("section_seq"),
                "section_from_clock": self.pair.get("from_clock"),
                "section_to_clock": self.pair.get("to_clock"),
                "slack": slack,
                "slack_status": match.group(1),
            }
            self.path_stats = {"next_seq": int(self.path["seq"]) + 1,
                               "sum_data": 0.0, "fanout": None, "src_site": None,
                               "dst_site": None, "n_seg": 0, "n_data": 0}
            self.mode = "path_head"
            return

        if self.path is None:
            return

        if line.startswith("    Location"):
            self.mode = "path_table"
            self.block_index = 0
            self.separators = 0
            self.seg_seq = 0
            self.pending = None
            return

        if self.mode == "path_head":
            match = RE_FIELD.match(line)
            if match:
                self.last_label = match.group(1).strip()
                self._path_field(self.last_label, match.group(2).strip())
                return
            match = RE_SUBF.match(line)
            if match:
                key = re.sub(r"\s+", " ", match.group(1).strip())
                self.path[SUBFIELD_MAP.get(key, key)] = _fnum(match.group(2))
                return
            if line.lstrip().startswith("("):
                cell, clock = (None, None)
                match = RE_CELL_CLOCK.search(line)
                if match:
                    cell, clock = match.group(1), match.group(2)
                if self.last_label == "Source":
                    self.path["source_cell_type"] = cell
                    self.path["source_clock"] = clock
                elif self.last_label == "Destination":
                    self.path["dest_cell_type"] = cell
                    self.path["dest_clock"] = clock
            return

        if self.mode == "path_table":
            stripped = line.strip()
            if stripped.startswith("required time"):
                self.path["required_ns"] = _fnum(stripped.split()[-1])
                return
            if stripped.startswith("arrival time"):
                printed = _fnum(stripped.split()[-1])
                self.path["arrival_ns"] = None if printed is None else -printed
                return
            if stripped.startswith("slack"):
                self._finish_path(lineno)
                return
            if RE_SEP2.match(line):
                self.separators = getattr(self, "separators", 0) + 1
                self.block_index = min(self.separators - 1, 2)
                return
            if RE_SEP.match(line):
                return
            if stripped.startswith("(clock"):
                return
            self._segment_line(line)

    def _path_field(self, label: str, value: str) -> None:
        """Store one ``key: value`` field of the path header.

        Args:
            label: Field name.
            value: Field value.
        """
        path = self.path
        if label == "Source":
            path["source_pin"] = value
        elif label == "Destination":
            path["dest_pin"] = value
        elif label == "Path Group":
            path["path_group"] = value
        elif label == "Path Type":
            path["path_type"] = value.split("(")[0].strip()
            match = re.search(r"\((Max|Min) at (\w+) Process Corner\)", value)
            path["max_min"] = match.group(1) if match else None
            path["corner"] = match.group(2) if match else None
        elif label == "Requirement":
            match = RE_REQUIREMENT.match(value)
            path["requirement_ns"] = _fnum(match.group(1)) if match else None
            path["requirement_text"] = match.group(2) if match and match.group(2) else value
        elif label == "Data Path Delay":
            match = RE_DATA_DELAY.match(value)
            if match:
                path["data_delay_ns"] = _fnum(match.group(1))
                path["logic_ns"] = _fnum(match.group(2))
                path["logic_pct"] = _fnum(match.group(3))
                path["route_ns"] = _fnum(match.group(4))
                path["route_pct"] = _fnum(match.group(5))
        elif label == "Logic Levels":
            match = RE_LEVELS.match(value)
            if match:
                path["logic_levels"] = int(match.group(1))
                path["logic_breakdown"] = match.group(2)
        elif label == "Clock Path Skew":
            path["skew_ns"] = _fnum(value.split("ns")[0])
        elif label == "Clock Uncertainty":
            path["uncertainty_ns"] = _fnum(value.split("ns")[0])
        elif label.startswith("Clock Net Delay (Source)"):
            match = RE_CLKNET.match(value)
            if match:
                path["clk_net_src_ns"] = _fnum(match.group(1))
                path["clk_net_src_route_ns"] = _fnum(match.group(2))
                path["clk_net_src_dist_ns"] = _fnum(match.group(3))
        elif label.startswith("Clock Net Delay (Destination)"):
            match = RE_CLKNET.match(value)
            if match:
                path["clk_net_dst_ns"] = _fnum(match.group(1))
                path["clk_net_dst_route_ns"] = _fnum(match.group(2))
                path["clk_net_dst_dist_ns"] = _fnum(match.group(3))
        elif label == "Timing Exception":
            path["timing_exception"] = value
        elif label == "Clock Domain Crossing":
            path["cdc_note"] = value
        else:
            path[label] = value

    def _segment_line(self, line: str) -> None:
        """Handle one line of the path detail table.

        Args:
            line: Raw report line.
        """
        record, self.pending = self._split_segment(line, self.pending)
        if record is None or self.path is None:
            return
        self.seg_seq += 1
        block = BLOCK_NAMES[self.block_index]
        record["path_seq"] = self.path["seq"]
        record["seq"] = self.seg_seq
        record["block"] = block
        self._segment_stats(block, record)
        if self._store_segments():
            record["resource_id"] = self.writer.resource_id(record.pop("resource"))
            self.writer.add_segment(record)
        else:
            record.pop("resource", None)

    def _store_segments(self) -> bool:
        """Return whether detail rows of the current path are stored.

        Returns:
            ``True`` when the current ``--keep-detail`` policy keeps this path.
        """
        if self.keep_detail == "all":
            return True
        if self.keep_detail == "negative":
            slack = self.path.get("slack") if self.path else None
            return slack is not None and slack < 0
        return False

    def _segment_stats(self, block: str, record: dict) -> None:
        """Update path level statistics from one detail row.

        Args:
            block: Detail block the row belongs to.
            record: Parsed detail row.
        """
        stats = self.path_stats
        stats["n_seg"] = int(stats.get("n_seg", 0)) + 1
        incr = record.get("incr_ns")
        if block == "data" and incr is not None:
            stats["sum_data"] = float(stats.get("sum_data", 0.0)) + incr
            stats["n_data"] = int(stats.get("n_data", 0)) + 1
        fanout = record.get("fanout")
        if fanout is not None:
            current = stats.get("fanout")
            stats["fanout"] = fanout if current is None else max(current, fanout)
        site = record.get("site")
        if site and record.get("cell_type"):
            if block == "launch_clock":
                stats["src_site"] = site
            elif block == "capture_clock" and stats.get("dst_site") is None:
                stats["dst_site"] = site

    @staticmethod
    def _split_segment(line: str, pending):
        """Split one detail table line into a record dict (or a pending pair)."""
        pending_loc, pending_type = pending if pending else (None, None)
        match = RE_NUM2.search(line[:72])
        if match:
            site, type_start = split_prefix(line)
            site = site or pending_loc
            delay_type = line[type_start:match.start()].strip() or pending_type
            rest = line[match.end():]
            transition = None
            edge = re.match(r"^\s*([rf])(?=\s|$)", rest)
            if edge:
                transition = edge.group(1)
                rest = rest[edge.end():]
            columns = [part for part in re.split(r"\s{2,}", rest.strip()) if part]
            pblock = None
            resource = None
            if len(columns) >= 2:
                pblock, resource = columns[0], columns[1]
            elif columns:
                resource = columns[0]
            is_net, cell_type, pin_func, fanout = split_delay_type(delay_type)
            return {
                "site": site,
                "cell_type": cell_type,
                "pin_func": pin_func if not is_net else None,
                "delay_type": delay_type,
                "is_net": int(is_net),
                "fanout": fanout,
                "incr_ns": _fnum(match.group(1)),
                "cum_ns": _fnum(match.group(2)),
                "transition": transition,
                "pblock": pblock,
                "resource": resource,
            }, None

        site, type_start = split_prefix(line)
        rest = line[type_start:].strip() or None
        if rest:
            pin_row = re.match(r"^(.+?)\s{2,}([rf])(?=\s|$)\s*(.*)$", rest)
            if pin_row:
                is_net, cell_type, pin_func, _ = split_delay_type(pin_row.group(1))
                return {
                    "site": site, "cell_type": cell_type, "pin_func": pin_func,
                    "delay_type": pin_row.group(1), "is_net": int(is_net), "fanout": None,
                    "incr_ns": None, "cum_ns": None, "transition": pin_row.group(2),
                    "pblock": None, "resource": pin_row.group(3) or None,
                }, None
            if site:
                return None, (site, rest)
            if pending_loc is None and pending_type is None:
                return None, (None, rest)
        return None, None

    def _finish_path(self, lineno: int) -> None:
        """Finish the current path and write it to the database.

        Args:
            lineno: Line number of the closing ``slack`` row.
        """
        path = self.path
        stats = self.path_stats
        path["line_end"] = lineno
        path["n_seg"] = stats.get("n_seg", 0)
        path["n_data_seg"] = stats.get("n_data", 0)
        path["fanout_max"] = stats.get("fanout")
        path["src_site"] = stats.get("src_site")
        path["dst_site"] = stats.get("dst_site")
        path["keep_detail"] = int(self._store_segments())

        source = path.get("source_pin")
        dest = path.get("dest_pin")
        path["source_cell"] = self._cell_name(source)
        path["dest_cell"] = self._cell_name(dest)
        path["src_hidden"] = int(source == "<hidden>")
        path["dst_hidden"] = int(dest == "<hidden>")
        path["path_key"] = "|".join(str(part) for part in (
            source, dest, path.get("path_group"), path.get("path_type"), path.get("max_min")))

        self.writer.add_path(path)
        if path.get("logic_breakdown"):
            for cell_type, count in RE_LEVEL_ITEM.findall(str(path["logic_breakdown"])):
                self.writer.add_logic_cell(path["seq"], cell_type, int(count))
        if self.hier:
            self._add_hierarchy(int(path["seq"]), "source", source)
            self._add_hierarchy(int(path["seq"]), "dest", dest)
        self._check_path(path, stats)
        self.path = None
        self.mode = "path_head"

    @staticmethod
    def _cell_name(pin: str | None) -> str | None:
        """Return the leaf cell name of a pin path.

        Args:
            pin: Full pin name such as ``a/b/reg[2]/C``.

        Returns:
            Cell name such as ``reg[2]``, or ``None`` for hidden names.
        """
        if not pin or pin == "<hidden>":
            return None
        head, _, _ = pin.rpartition("/")
        return head.rsplit("/", 1)[-1] if head else None

    def _add_hierarchy(self, seq: int, side: str, pin: str | None) -> None:
        """Emit hierarchy prefixes of one endpoint.

        Args:
            seq: Path sequence number.
            side: ``source`` or ``dest``.
            pin: Full pin name.
        """
        if not pin or pin == "<hidden>":
            return
        parts = [part for part in pin.split("/") if part]
        for level, _ in enumerate(parts[:-1], start=1):
            self.writer.add_hier(seq, side, level, "/".join(parts[:level]))

    def _check_path(self, path: dict, stats: dict) -> None:
        """Run consistency checks on one finished path.

        Args:
            path: Path row being written.
            stats: Accumulated segment statistics of the path.
        """
        required = path.get("required_ns")
        arrival = path.get("arrival_ns")
        slack = path.get("slack")
        if required is not None and arrival is not None and slack is not None:
            if abs(required - arrival - slack) > 0.002:
                self._issue(path["line_start"], "invariant", "slack_mismatch",
                            f"required - arrival = {required - arrival:.3f}, slack = {slack:.3f}")
        if path.get("timing_exception") or path.get("data_delay_ns") is None:
            return
        total = float(stats.get("sum_data", 0.0))
        tolerance = 0.001 * int(stats.get("n_data", 0)) + 0.002
        if abs(total - float(path["data_delay_ns"])) > tolerance:
            self._issue(path["line_start"], "warning", "delay_mismatch",
                        f"sum(incr)={total:.3f} != data_delay={float(path['data_delay_ns']):.3f}")

    def _issue(self, lineno: int | None, severity: str, code: str, message: str) -> None:
        """Record one import problem.

        Args:
            lineno: Report line the problem was detected on.
            severity: ``warning`` or ``error``.
            code: Stable problem code.
            message: Human readable description.
        """
        self.result.issues.append((lineno, severity, code, message))
        self.writer.add_issue(lineno, severity, code, message, None)

    # -- pulse width --------------------------------------------------------
    def _pw_line(self, line: str) -> None:
        """Handle a Pulse Width Checks row.

        Args:
            line: Raw report line.
        """
        if line.startswith("Clock Name:"):
            self.pw_clock = line.split(":", 1)[1].strip()
            return
        if line.startswith("Check Type"):
            self.pw_header = True
            return
        if not self.pw_header or line.startswith(" "):
            return
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 8:
            return
        site = parts[7] if len(parts) >= 9 else None
        pin = parts[-1]
        self.pw_id += 1
        self.writer.add_pw_check({
            "id": self.pw_id,
            "seq": self.pw_id,
            "section_seq": self.pair.get("section_seq"),
            "clock_name": self.pw_clock,
            "check_type": parts[0],
            "corner": parts[1],
            "lib_pin": parts[2],
            "ref_pin": parts[3],
            "required_ns": _fnum(parts[4]),
            "actual_ns": _fnum(parts[5]),
            "slack_ns": _fnum(parts[6]),
            "site": site,
            "pin_id": self.writer.resource_id(pin),
        })

    # -- report row ---------------------------------------------------------
    def _flush_pair(self) -> None:
        """Write the pending clock pair summary row, if any.
        """
        if not self.pair:
            return
        self.writer.add_clock_pair(self.pair)
        self.pair = {}

    def _write_report(self) -> None:
        """Write the single ``report`` row from parsed metadata.
        """
        command = self.meta.get("Command", "")
        delay_type = self._command_option(command, "delay_type")
        max_paths = self._command_option(command, "max_paths")
        design_state = self.meta.get("Design State")
        run_date = self._parse_date(self.meta.get("Date"))
        self.writer.add_report({
            "singleton": 1,
            "source_file": str(self.report_path),
            "file_sha256": self._sha,
            "file_size": self.report_path.stat().st_size,
            "stage": self._stage(design_state),
            "tool_version": self.meta.get("Tool Version"),
            "run_date": run_date,
            "host": self.meta.get("Host"),
            "command": command,
            "design_name": self.meta.get("Design"),
            "device": self.meta.get("Device"),
            "speed_file": self.meta.get("Speed File"),
            "design_state": design_state,
            "temp_grade": self.meta.get("Temperature Grade"),
            "delay_type": delay_type,
            "max_paths": int(max_paths) if max_paths and max_paths.isdigit() else None,
            "constraints_met": self.constraints_met,
            "n_paths": self.writer.counts["paths"],
            "n_segments": self.writer.counts["segments"],
            "n_pw_checks": self.writer.counts["pw"],
            **{key: self.design.get(key) for key in (
                "wns", "tns", "tns_failing", "tns_total",
                "wpws", "tpws", "tpws_failing", "tpws_total")},
        })

    @staticmethod
    def _command_option(command: str, option: str) -> str | None:
        """Return the value of one option of the report command.

        Args:
            command: Full ``report_timing_summary`` command line.
            option: Option name without the leading dash.

        Returns:
            Option value or ``None``.
        """
        match = re.search(rf"-{option}\s+(\S+)", command)
        return match.group(1) if match else None

    @staticmethod
    def _parse_date(text: str | None) -> str | None:
        """Convert the report date to ISO 8601.

        Args:
            text: Date string as printed in the report header.

        Returns:
            ISO 8601 timestamp, or the original text when parsing fails.
        """
        if not text:
            return None
        normalized = re.sub(r"\s+", " ", text.strip())
        try:
            return datetime.strptime(normalized, "%a %b %d %H:%M:%S %Y").isoformat()
        except ValueError:
            return text

    @staticmethod
    def _stage(design_state: str | None) -> str | None:
        """Map the Vivado design state to a short stage name.

        Args:
            design_state: Value of the ``Design State`` header field.

        Returns:
            ``synth``, ``post_place``, ``post_route`` or ``None``.
        """
        if not design_state:
            return None
        state = design_state.lower()
        if "postroute" in state:
            return "post_route"
        if "postplace" in state:
            return "post_place"
        if "synth" in state:
            return "synth"
        return None


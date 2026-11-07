"""Per-path state machine for the timing details section."""

from __future__ import annotations

import re

from ..db import DbWriter
from .lexer import (
    BLOCK_NAMES,
    RE_CELL_CLOCK,
    RE_FIELD,
    RE_SEP,
    RE_SEP2,
    RE_SLACK,
    RE_SUBF,
    SUBFIELD_MAP,
    _fnum,
)
from .path_fields import apply_path_field
from .path_writer import finish_path, should_store
from .segments import split_segment, update_stats


class PathBlock:
    """Accumulate one path block and emit it when the slack row closes."""

    def __init__(self, writer: DbWriter, keep_detail: str, hier: bool,
                 issues: list) -> None:
        """Initialise the path block.

        Args:
            writer: Destination for path, segment and hierarchy rows.
            keep_detail: ``all``, ``negative`` or ``none``.
            hier: Whether to emit hierarchy prefixes.
            issues: Shared list receiving ``(line, severity, code, message)``.
        """
        self.writer = writer
        self.keep_detail = keep_detail
        self.hier = hier
        self.issues = issues

        self.active = False
        self.pair: dict[str, object] = {}
        self.path: dict[str, object] | None = None
        self.stats: dict[str, object] = {}
        self.next_seq = 1
        self.mode: str | None = None
        self.block_index = 0
        self.separators = 0
        self.seg_seq = 0
        self.pending: tuple[str | None, str | None] | None = None
        self.last_label: str | None = None

    def set_pair(self, pair: dict[str, object]) -> None:
        """Attach the clock pair that owns the next paths.

        Args:
            pair: Mutable clock pair summary dictionary.
        """
        self.pair = pair

    def start_section(self) -> None:
        """Start a ``Max Delay Paths`` / ``Min Delay Paths`` section."""
        self.active = True
        self.mode = "path_head"
        self.block_index = 0

    def reset(self) -> None:
        """Forget the current path and leave path mode."""
        self.active = False
        self.path = None
        self.mode = None
        self.pending = None
        self.last_label = None
        self.block_index = 0
        self.separators = 0
        self.seg_seq = 0
        self.stats = {}

    def feed(self, lineno: int, line: str) -> None:
        """Handle one line of a path block.

        Args:
            lineno: 1-based line number in the report.
            line: Raw report line.
        """
        if not self.active:
            return
        if line.startswith("Slack ("):
            match = RE_SLACK.match(line)
            if not match:
                return
            slack = _fnum(match.group(2))
            seq = self.next_seq
            self.next_seq = seq + 1
            self.path = {
                "seq": seq,
                "line_start": lineno,
                "line_end": None,
                "section_seq": self.pair.get("section_seq"),
                "section_from_clock": self.pair.get("from_clock"),
                "section_to_clock": self.pair.get("to_clock"),
                "slack": slack,
                "slack_status": match.group(1),
            }
            self.stats = {"sum_data": 0.0, "fanout": None, "src_site": None,
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
                apply_path_field(self.path, self.last_label, match.group(2).strip())
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
                self.separators += 1
                self.block_index = min(self.separators - 1, 2)
                return
            if RE_SEP.match(line):
                return
            if stripped.startswith("(clock"):
                return
            self._segment_line(line)

    def _segment_line(self, line: str) -> None:
        """Handle one line of the path detail table.

        Args:
            line: Raw report line.
        """
        record, self.pending = split_segment(line, self.pending)
        if record is None or self.path is None:
            return
        self.seg_seq += 1
        block = BLOCK_NAMES[self.block_index]
        record["path_seq"] = self.path["seq"]
        record["seq"] = self.seg_seq
        record["block"] = block
        update_stats(self.stats, block, record)
        if should_store(self.keep_detail, self.path):
            record["resource_id"] = self.writer.resource_id(record.pop("resource"))
            self.writer.add_segment(record)
        else:
            record.pop("resource", None)

    def _finish_path(self, lineno: int) -> None:
        """Finish the current path and write it to the database.

        Args:
            lineno: Line number of the closing ``slack`` row.
        """
        path = self.path
        if path is None:
            return
        finish_path(self.writer, path, self.stats, lineno,
                    self.keep_detail, self.hier, self.issues)
        self.path = None
        self.mode = "path_head"

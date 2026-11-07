"""Timing details section: clock pairs, path blocks and pulse width checks."""

from __future__ import annotations

import re

from ..db import DbWriter
from .lexer import RE_PAIR, _fnum, _inum
from .path_block import PathBlock


class DetailsParser:
    """Parse the ``Timing Details`` section of a report."""

    def __init__(self, writer: DbWriter, keep_detail: str, hier: bool,
                 issues: list) -> None:
        """Initialise the details parser.

        Args:
            writer: Destination for clock pair, path and PW rows.
            keep_detail: ``all``, ``negative`` or ``none``.
            hier: Whether to emit hierarchy prefixes.
            issues: Shared list receiving ``(line, severity, code, message)``.
        """
        self.writer = writer
        self.section_seq = 0
        self.pair: dict[str, object] = {}
        self.mode: str | None = None
        self.pw_header = False
        self.pw_clock: str | None = None
        self.pw_id = 0
        self.path_block = PathBlock(writer, keep_detail, hier, issues)

    def feed(self, lineno: int, line: str) -> None:
        """Handle one line inside the Timing Details section.

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
            self.path_block.reset()
            self.path_block.set_pair(self.pair)
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
            self.path_block.start_section()
            return
        if stripped == "Pulse Width Checks":
            self.mode = "pw_head"
            self.pw_header = False
            self.path_block.reset()
            return
        if self.mode in ("path_head", "path_table"):
            self.path_block.feed(lineno, line)
        elif self.mode == "pw_head":
            self._pw_line(line)

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

    def _flush_pair(self) -> None:
        """Write the pending clock pair summary row, if any."""
        if not self.pair:
            return
        self.writer.add_clock_pair(self.pair)
        self.pair = {}

    def finish(self) -> None:
        """Flush the last clock pair summary row."""
        self._flush_pair()

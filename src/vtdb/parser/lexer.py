"""Line-level regexes and pure splitters for the Vivado report format."""

from __future__ import annotations

import re

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

BLOCK_NAMES = ("launch_clock", "data", "capture_clock")
LOC_START = 4
TYPE_START = 25

SUBFIELD_MAP = {
    "Destination Clock Delay (DCD)": "dcd_ns",
    "Source Clock Delay (SCD)": "scd_ns",
    "Clock Pessimism Removal (CPR)": "cpr_ns",
    "Total System Jitter (TSJ)": "tsj_ns",
    "Total Input Jitter (TIJ)": "tij_ns",
    "Discrete Jitter (DJ)": "dj_ns",
    "Phase Error (PE)": "pe_ns",
}


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
    """Split a ``Delay type`` cell into its parts.

    Args:
        text: Delay type cell such as ``FDRE (Prop_FDRE_C_Q)`` or
            ``net (fo=3, unplaced)``.

    Returns:
        ``(is_net, cell_type, pin_func, fanout)``.
    """
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

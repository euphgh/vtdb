"""Detail-row parsing and path statistics for the timing parser."""

from __future__ import annotations

import re

from .lexer import RE_NUM2, _fnum, split_delay_type, split_prefix
from .model import PathStats


def split_segment(line: str, pending):
    """Split one detail table line into a record or a pending pair.

    Long ``Location + Delay type`` cells are printed on one line while the
    numeric columns follow on the next line.  The caller keeps the returned
    pending pair and passes it to the next call.

    Args:
        line: Raw report line.
        pending: ``(location, delay_type)`` left over from the previous line,
            or ``None``.

    Returns:
        ``(record, pending)`` where ``record`` is a parsed detail row or
        ``None``.
    """
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


def update_stats(stats: PathStats, block: str, record: dict) -> None:
    """Update path level statistics from one detail row.

    Args:
        stats: Mutable statistics of the current path.
        block: Detail block the row belongs to.
        record: Parsed detail row.
    """
    stats.n_seg += 1
    incr = record.get("incr_ns")
    if block == "data" and incr is not None:
        stats.sum_data += incr
        stats.n_data += 1
    fanout = record.get("fanout")
    if fanout is not None:
        stats.fanout = fanout if stats.fanout is None else max(stats.fanout, fanout)
    site = record.get("site")
    if site and record.get("cell_type"):
        if block == "launch_clock":
            stats.src_site = site
        elif block == "capture_clock" and stats.dst_site is None:
            stats.dst_site = site

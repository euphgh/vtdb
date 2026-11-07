"""Column definitions used by the CLI query commands."""

from __future__ import annotations

from ..output import Column

TOP_COLUMNS: list[Column] = [
    ("seq", "#", "int"),
    ("slack", "SLACK", "f3"),
    ("path_group", "GROUP", None),
    ("logic_levels", "LVL", "int"),
    ("route_pct", "ROUTE%", "f1"),
    ("source", "SOURCE", None),
    ("dest", "DEST", None),
    ("note", "NOTE", None),
]

GROUP_COLUMNS: list[Column] = [
    ("grp", "GROUP", None),
    ("paths", "PATHS", "int"),
    ("fail", "FAIL", "int"),
    ("wns", "WNS", "f3"),
    ("tns_sample", "TNS(SAMPLE)", "f3"),
    ("route_pct", "ROUTE%", "f1"),
]

CLOCK_PAIR_COLUMNS: list[Column] = [
    ("from_clock", "FROM", None),
    ("to_clock", "TO", None),
    ("paths", "PATHS", "int"),
    ("fail", "FAIL", "int"),
    ("wns", "WNS", "f3"),
    ("tns_sample", "TNS(SAMPLE)", "f3"),
    ("route_pct", "ROUTE%", "f1"),
]

MODULE_COLUMNS: list[Column] = [
    ("prefix", "PREFIX", None),
    ("paths", "PATHS", "int"),
    ("fail", "FAIL", "int"),
    ("wns", "WNS", "f3"),
    ("tns_sample", "TNS(SAMPLE)", "f3"),
]

SEGMENT_COLUMNS: list[Column] = [
    ("block", "BLOCK", None),
    ("seq", "SEQ", "int"),
    ("site", "SITE", None),
    ("cell_type", "CELL", None),
    ("pin_func", "ARC", None),
    ("incr_ns", "INCR", "f3"),
    ("cum_ns", "CUM", "f3"),
    ("fanout", "FO", "int"),
    ("resource", "RESOURCE", None),
]

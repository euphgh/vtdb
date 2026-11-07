"""Header field parsing for one timing path."""

from __future__ import annotations

import re

from .lexer import RE_CLKNET, RE_DATA_DELAY, RE_LEVELS, RE_REQUIREMENT, _fnum


def apply_path_field(path: dict, label: str, value: str) -> None:
    """Store one ``key: value`` field of the path header.

    Args:
        path: Mutable path row being built.
        label: Field name.
        value: Field value.
    """
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

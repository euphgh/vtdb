"""Construction of the single ``report`` table row."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..db import DbWriter


def command_option(command: str, option: str) -> str | None:
    """Return the value of one option of the report command.

    Args:
        command: Full ``report_timing_summary`` command line.
        option: Option name without the leading dash.

    Returns:
        Option value or ``None``.
    """
    match = re.search(rf"-{option}\s+(\S+)", command)
    return match.group(1) if match else None


def parse_date(text: str | None) -> str | None:
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


def stage(design_state: str | None) -> str | None:
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


def write_report(writer: DbWriter, report_path: Path, sha: str,
                 meta: dict[str, str], design: dict[str, float | int | None],
                 constraints_met: int | None, counts: dict[str, int]) -> None:
    """Add the single ``report`` row from parsed metadata.

    Args:
        writer: Destination for the report row.
        report_path: Report file that was parsed.
        sha: SHA-256 digest of the report file.
        meta: Header metadata of the report.
        design: Design Timing Summary values.
        constraints_met: ``1`` when constraints are met, ``0`` otherwise.
        counts: Row counts collected by the writer.
    """
    command = meta.get("Command", "")
    delay_type = command_option(command, "delay_type")
    max_paths = command_option(command, "max_paths")
    design_state = meta.get("Design State")
    run_date = parse_date(meta.get("Date"))
    writer.add_report({
        "singleton": 1,
        "source_file": str(report_path),
        "file_sha256": sha,
        "file_size": report_path.stat().st_size,
        "stage": stage(design_state),
        "tool_version": meta.get("Tool Version"),
        "run_date": run_date,
        "host": meta.get("Host"),
        "command": command,
        "design_name": meta.get("Design"),
        "device": meta.get("Device"),
        "speed_file": meta.get("Speed File"),
        "design_state": design_state,
        "temp_grade": meta.get("Temperature Grade"),
        "delay_type": delay_type,
        "max_paths": int(max_paths) if max_paths and max_paths.isdigit() else None,
        "constraints_met": constraints_met,
        "n_paths": counts["paths"],
        "n_segments": counts["segments"],
        "n_pw_checks": counts["pw"],
        **{key: design.get(key) for key in (
            "wns", "tns", "tns_failing", "tns_total",
            "wpws", "tpws", "tpws_failing", "tpws_total")},
    })

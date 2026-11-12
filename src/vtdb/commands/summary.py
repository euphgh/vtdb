"""Implementation of the ``vtdb summary`` command."""

from __future__ import annotations

import argparse
import json

from .. import queries
from .common import EXIT_DB, EXIT_OK, CliError, fmt3, open_query_db


def _int(value) -> str:
    """Format an integer, or ``-`` when missing.

    Args:
        value: Value to format.

    Returns:
        Display text.
    """
    return "-" if value is None else str(int(value))


def _constraints(value) -> str:
    """Format the constraint status for text output.

    Args:
        value: ``True``, ``False`` or ``None``.

    Returns:
        ``met``, ``not met`` or ``-``.
    """
    if value is True:
        return "met"
    if value is False:
        return "not met"
    return "-"


def _by_type(rows: list) -> str:
    """Format the path-by-type counts.

    Args:
        rows: ``path_type / keep_detail / count`` rows.

    Returns:
        Space separated ``type=count`` pairs, or ``-``.
    """
    if not rows:
        return "-"
    return "  ".join(f"{row['path_type'] or '?'}={row['count']}" for row in rows)


def _issues(rows: list) -> str:
    """Format the grouped import issue counts.

    Args:
        rows: ``severity / code / count`` rows.

    Returns:
        ``severity/code=count`` pairs joined by ``; ``.
    """
    if not rows:
        return "-"
    return "; ".join(
        f"{row['severity'] or '?'}/{row['code'] or '?'}={row['count']}"
        for row in rows)


def build_summary(conn) -> dict:
    """Read one database and assemble the three-section summary.

    Args:
        conn: Read-only database connection.

    Returns:
        ``metadata`` / ``reported`` / ``imported`` sections.

    Raises:
        CliError: When the database has no ``report`` row.
    """
    report = conn.execute(*queries.summary_report_sql()).fetchone()
    if report is None:
        raise CliError("no report row in this database", EXIT_DB)
    path_rows = conn.execute(*queries.summary_paths_sql()).fetchall()
    issue_rows = conn.execute(*queries.summary_issues_sql()).fetchall()

    total = report["n_paths"] if report["n_paths"] is not None else sum(
        row["count"] for row in path_rows)
    with_detail = sum(row["count"] for row in path_rows if row["keep_detail"])

    constraints = report["constraints_met"]
    if constraints == 1:
        constraints = True
    elif constraints == 0:
        constraints = False
    else:
        constraints = None

    return {
        "metadata": {
            "source_file": report["source_file"],
            "file_sha256": report["file_sha256"],
            "file_size": report["file_size"],
            "stage": report["stage"],
            "tool_version": report["tool_version"],
            "run_date": report["run_date"],
            "host": report["host"],
            "command": report["command"],
            "design_name": report["design_name"],
            "device": report["device"],
            "speed_file": report["speed_file"],
            "design_state": report["design_state"],
            "temp_grade": report["temp_grade"],
            "imported_at": report["imported_at"],
        },
        "reported": {
            "setup": {
                "wns": report["wns"],
                "tns": report["tns"],
                "failing_endpoints": report["tns_failing"],
                "total_endpoints": report["tns_total"],
            },
            "hold": {
                "available": False,
                "reason": "hold summary not captured",
            },
            "pulse_width": {
                "wpws": report["wpws"],
                "tpws": report["tpws"],
                "tpws_failing": report["tpws_failing"],
                "tpws_total": report["tpws_total"],
            },
            "constraints_met": constraints,
        },
        "imported": {
            "paths": total,
            "paths_with_detail": with_detail,
            "paths_without_detail": total - with_detail,
            "paths_by_type": [
                {"path_type": row["path_type"],
                 "keep_detail": row["keep_detail"],
                 "count": row["count"]}
                for row in path_rows
            ],
            "segments": report["n_segments"],
            "pw_checks": report["n_pw_checks"],
            "delay_type": report["delay_type"],
            "max_paths": report["max_paths"],
            "issues": [
                {"severity": row["severity"], "code": row["code"],
                 "count": row["count"]}
                for row in issue_rows
            ],
        },
    }


def _render_table(data: dict) -> list[str]:
    """Render the summary as grouped key-value lines.

    Args:
        data: Summary returned by :func:`build_summary`.

    Returns:
        Text lines without trailing newlines.
    """
    meta = data["metadata"]
    reported = data["reported"]
    imported = data["imported"]

    design = meta["design_name"] or "-"
    if meta["design_state"]:
        design = f"{design} ({meta['design_state']})"

    setup = reported["setup"]
    pulse = reported["pulse_width"]
    max_paths = imported["max_paths"]

    lines = ["metadata"]
    for label, value in (
        ("design", design),
        ("report", meta["source_file"] or "-"),
        ("tool", meta["tool_version"] or "-"),
        ("date", meta["run_date"] or "-"),
        ("host", meta["host"] or "-"),
        ("device", meta["device"] or "-"),
        ("speed file", meta["speed_file"] or "-"),
        ("temperature", meta["temp_grade"] or "-"),
        ("command", meta["command"] or "-"),
    ):
        lines.append(f"  {label:<15}{value}")

    lines.append("")
    lines.append("reported")
    lines.append(
        f"  {'setup':<15}WNS {fmt3(setup['wns'])} ns  TNS {fmt3(setup['tns'])} ns"
        f"  failing {_int(setup['failing_endpoints'])}"
        f"  total {_int(setup['total_endpoints'])}")
    hold = reported["hold"]
    lines.append(f"  {'hold':<15}unavailable ({hold['reason']})")
    lines.append(
        f"  {'pulse width':<15}WPWS {fmt3(pulse['wpws'])} ns"
        f"  TPWS {fmt3(pulse['tpws'])} ns"
        f"  failing {_int(pulse['tpws_failing'])}"
        f"  total {_int(pulse['tpws_total'])}")
    lines.append(f"  {'constraints':<15}{_constraints(reported['constraints_met'])}")

    lines.append("")
    lines.append("imported")
    lines.append(f"  {'paths':<15}{_int(imported['paths'])}")
    lines.append(f"  {'with detail':<15}{_int(imported['paths_with_detail'])}")
    lines.append(f"  {'without detail':<15}{_int(imported['paths_without_detail'])}")
    lines.append(f"  {'by type':<15}{_by_type(imported['paths_by_type'])}")
    lines.append(f"  {'segments':<15}{_int(imported['segments'])}")
    lines.append(f"  {'pw checks':<15}{_int(imported['pw_checks'])}")
    lines.append(f"  {'delay type':<15}{imported['delay_type'] or '-'}")
    lines.append(
        f"  {'max paths':<15}"
        f"{_int(max_paths) if max_paths is not None else '-'}"
        + (" (report option)" if max_paths is not None else ""))
    lines.append(f"  {'issues':<15}{_issues(imported['issues'])}")
    return lines


def cmd_summary(args: argparse.Namespace) -> int:
    """Handle ``vtdb summary``.

    Args:
        args: Parsed command line arguments.

    Returns:
        Process exit code.
    """
    conn = open_query_db(args)
    data = build_summary(conn)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False))
    else:
        for line in _render_table(data):
            print(line)
    return EXIT_OK

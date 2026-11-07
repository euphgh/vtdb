"""Parser unit and characterization tests.

These tests describe the current parser contract: how raw report lines are
split into records, which derived fields reach the database, and how the
consistency checks behave.  They are the safety net for splitting
``parser.py`` into a package, so they assert current behaviour rather than an
idealized one.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "data" / "mini_timing.rpt"
EDGE_FIXTURE = ROOT / "tests" / "data" / "edge_timing.rpt"

sys.path.insert(0, str(SRC))

from vtdb.db import DbWriter, create_memory_database  # noqa: E402
from vtdb.parser import (  # noqa: E402
    ParseResult,
    ReportParser,
    split_delay_type,
    split_prefix,
)


def parse_report(path: Path) -> tuple[sqlite3.Connection, ParseResult]:
    """Parse ``path`` into an in-memory database.

    Args:
        path: Report file to parse.

    Returns:
        Open connection and the parser result.
    """
    conn = create_memory_database()
    conn.row_factory = sqlite3.Row
    writer = DbWriter(conn)
    result = ReportParser(path, writer).run()
    writer.close()
    return conn, result


def parse_text(tmp_path: Path, text: str) -> tuple[sqlite3.Connection, ParseResult]:
    """Write ``text`` to a temporary report and parse it.

    Args:
        tmp_path: Pytest temporary directory.
        text: Complete report text.

    Returns:
        Open connection and the parser result.
    """
    path = tmp_path / "report.rpt"
    path.write_text(text, encoding="utf-8")
    return parse_report(path)


def fixture_line(number: int) -> str:
    """Return one one-based line of the mini fixture.

    Args:
        number: One-based line number.

    Returns:
        Line without the trailing newline.
    """
    return FIXTURE.read_text(encoding="utf-8").splitlines()[number - 1]


# -- pure line splitting ----------------------------------------------------
def test_split_prefix_fixed_and_blank_location() -> None:
    """Location starts at column 4 and the delay type at column 25."""
    fixed = fixture_line(123)
    site, start = split_prefix(fixed)
    assert site == "SLICE_X0Y0"
    assert start == 25
    assert fixed[start:].lstrip().startswith("FDRE")

    blank = fixture_line(122)
    site, start = split_prefix(blank)
    assert site is None
    assert start == 25
    assert blank[start:].lstrip().startswith("net")


def test_split_prefix_detects_shifted_location() -> None:
    """A long location pushes the delay type past column 25."""
    line = "    VERY_LONG_SITE_NAME   LUT6 (Prop_LUT6_I1_O)"
    site, start = split_prefix(line)
    assert site == "VERY_LONG_SITE_NAME"
    assert start == 26
    assert line[start:].startswith("LUT6")


def test_split_delay_type_variants() -> None:
    """Nets, cells, pins and plain labels are classified explicitly."""
    assert split_delay_type(None) == (False, None, None, None)
    assert split_delay_type("net (fo=10, unplaced)") == (
        True, None, "net (fo=10, unplaced)", 10)
    assert split_delay_type("FDRE (Prop_FDRE_C_Q)") == (
        False, "FDRE", "Prop_FDRE_C_Q", None)
    assert split_delay_type("FDRE") == (False, "FDRE", None, None)
    assert split_delay_type("clock pessimism") == (
        False, None, "clock pessimism", None)


def test_split_segment_full_row() -> None:
    """A full detail row carries location, arc, timing and resource."""
    record, pending = ReportParser._split_segment(fixture_line(125), None)
    assert pending is None
    assert record is not None
    assert record["site"] is None
    assert record["cell_type"] == "FDRE"
    assert record["pin_func"] == "Prop_FDRE_C_Q"
    assert record["incr_ns"] == pytest.approx(0.100)
    assert record["cum_ns"] == pytest.approx(0.140)
    assert record["transition"] == "r"
    assert record["resource"] == "u_a/reg_a_reg[0]/Q"
    assert record["is_net"] == 0


def test_split_segment_pin_row() -> None:
    """A pin row has a location and transition but no delay columns."""
    record, pending = ReportParser._split_segment(fixture_line(123), None)
    assert pending is None
    assert record is not None
    assert record["site"] == "SLICE_X0Y0"
    assert record["cell_type"] == "FDRE"
    assert record["pin_func"] is None
    assert record["incr_ns"] is None
    assert record["cum_ns"] is None
    assert record["transition"] == "r"
    assert record["resource"] == "u_a/reg_a_reg[0]/C"


def test_split_segment_clock_edge_row() -> None:
    """A clock edge row has numbers but no location or delay type."""
    record, pending = ReportParser._split_segment(fixture_line(120), None)
    assert pending is None
    assert record is not None
    assert record["site"] is None
    assert record["cell_type"] is None
    assert record["delay_type"] is None
    assert record["incr_ns"] == pytest.approx(0.0)
    assert record["cum_ns"] == pytest.approx(0.0)
    assert record["transition"] == "r"
    assert record["resource"] is None


def test_split_segment_net_row() -> None:
    """A net row is tagged as a net and keeps its fanout."""
    record, pending = ReportParser._split_segment(fixture_line(122), None)
    assert pending is None
    assert record is not None
    assert record["is_net"] == 1
    assert record["cell_type"] is None
    assert record["fanout"] == 10
    assert record["incr_ns"] == pytest.approx(0.040)
    assert record["resource"] == "u_a/clk"


def test_split_segment_wrapped_row_merges_pending_location() -> None:
    """A wrapped Location + Delay type pair is merged into the next line."""
    record, pending = ReportParser._split_segment(fixture_line(128), None)
    assert record is None
    assert pending == ("SLICE_X1Y2", "LUT6 (Prop_LUT6_I1_O)")

    record, pending = ReportParser._split_segment(fixture_line(129), pending)
    assert pending is None
    assert record is not None
    assert record["site"] == "SLICE_X1Y2"
    assert record["delay_type"] == "LUT6 (Prop_LUT6_I1_O)"
    assert record["cell_type"] == "LUT6"
    assert record["incr_ns"] == pytest.approx(2.000)
    assert record["cum_ns"] == pytest.approx(4.540)
    assert record["transition"] == "r"
    assert record["resource"] == "u_a/g2/O"


# -- fixture level characterization ----------------------------------------
def test_fixture_counts_and_report_row() -> None:
    """The fixture produces the documented row counts and metadata."""
    conn, result = parse_report(FIXTURE)
    assert result.counts == {"paths": 2, "segments": 23, "pw": 3}
    assert result.issues == []

    report = conn.execute("SELECT * FROM report").fetchone()
    assert report["design_name"] == "mini_top"
    assert report["design_state"] == "Synthesized"
    assert report["stage"] == "synth"
    assert report["constraints_met"] == 0
    assert report["delay_type"] == "max"
    assert report["max_paths"] == 1000
    assert report["run_date"] == "2026-11-03T15:08:49"
    assert report["n_paths"] == 2
    assert report["n_segments"] == 23
    assert report["n_pw_checks"] == 3
    assert report["wns"] == pytest.approx(-1.234)
    assert report["tns"] == pytest.approx(-1.234)
    assert report["source_file"].endswith("mini_timing.rpt")
    assert len(report["file_sha256"]) == 64


def test_path_rows_preserve_summary_and_derived_fields() -> None:
    """Path rows keep endpoints, sites, fanout and the arrival sign fix."""
    conn, _ = parse_report(FIXTURE)
    first, second = conn.execute("SELECT * FROM path ORDER BY seq").fetchall()

    assert first["slack"] == pytest.approx(-1.234)
    assert first["required_ns"] == pytest.approx(4.035)
    assert first["arrival_ns"] == pytest.approx(5.269)
    assert first["required_ns"] - first["arrival_ns"] == pytest.approx(first["slack"])
    assert first["source_pin"] == "u_a/reg_a_reg[0]/C"
    assert first["source_cell"] == "reg_a_reg[0]"
    assert first["source_cell_type"] == "FDRE"
    assert first["dest_pin"] == "u_b/reg_b_reg[0]/D"
    assert first["dest_cell"] == "reg_b_reg[0]"
    assert first["dest_cell_type"] == "FDRE"
    assert first["src_site"] == "SLICE_X0Y0"
    assert first["dst_site"] == "SLICE_X5Y5"
    assert first["fanout_max"] == 12
    assert first["n_seg"] == 16
    assert first["n_data_seg"] == 5
    assert first["section_from_clock"] == "clk_a"
    assert first["section_to_clock"] == "clk_a"
    assert first["src_hidden"] == 0
    assert first["dst_hidden"] == 0

    assert second["slack"] == pytest.approx(0.250)
    assert second["source_cell"] == "short_reg[0]"
    assert second["dest_cell"] == "sink_reg[0]"
    # Characterization: the launch block of path 2 has no cell row, so the
    # parser cannot infer a source site.
    assert second["src_site"] is None
    assert second["dst_site"] is None
    assert second["fanout_max"] == 66140
    assert second["n_data_seg"] == 4


def test_hierarchy_logic_and_segment_blocks() -> None:
    """Derived hierarchy, logic cells and segment blocks match the report."""
    conn, _ = parse_report(FIXTURE)

    assert conn.execute("SELECT COUNT(*) FROM hier_prefix").fetchone()[0] == 8
    nodes = {row["prefix"] for row in conn.execute("SELECT prefix FROM hier_node")}
    assert "u_a/reg_a_reg[0]" in nodes
    assert "u_d/sink_reg[0]" in nodes

    logic = {
        (row["path_seq"], row["cell_type"]): row["count"]
        for row in conn.execute("SELECT * FROM logic_cell")
    }
    assert logic == {(1, "LUT6"): 2, (1, "FDRE"): 1, (2, "LUT6"): 1}

    blocks = {
        (row["path_seq"], row["block"]): row["n"]
        for row in conn.execute(
            "SELECT path_seq, block, COUNT(*) AS n FROM path_segment"
            " GROUP BY path_seq, block")
    }
    assert blocks == {
        (1, "launch_clock"): 4,
        (1, "data"): 6,
        (1, "capture_clock"): 6,
        (2, "launch_clock"): 1,
        (2, "data"): 5,
        (2, "capture_clock"): 1,
    }

    wrapped = conn.execute(
        "SELECT s.*, r.name AS resource FROM path_segment s"
        " JOIN resource r ON r.id = s.resource_id"
        " WHERE s.path_seq = 1 AND s.site = 'SLICE_X1Y2'"
    ).fetchone()
    assert wrapped["cell_type"] == "LUT6"
    assert wrapped["incr_ns"] == pytest.approx(2.000)
    assert wrapped["resource"] == "u_a/g2/O"


def test_pulse_width_rows_are_parsed() -> None:
    """Pulse width rows keep clock, check type, timing and site."""
    conn, _ = parse_report(FIXTURE)
    rows = conn.execute("SELECT * FROM pw_check ORDER BY id").fetchall()
    assert len(rows) == 3

    first = rows[0]
    assert first["clock_name"] == "clk_a"
    assert first["check_type"] == "Min Period"
    assert first["corner"] == "n/a"
    assert first["lib_pin"] == "FDRE/C"
    assert first["ref_pin"] == "n/a"
    assert first["required_ns"] == pytest.approx(0.550)
    assert first["actual_ns"] == pytest.approx(4.000)
    assert first["slack_ns"] == pytest.approx(3.450)
    assert first["site"] == "SLICE_X1Y2"
    assert first["section_seq"] == 1
    pin = conn.execute(
        "SELECT name FROM resource WHERE id = ?", (first["pin_id"],)).fetchone()[0]
    assert pin == "u_a/reg_a_reg[0]/C"


def test_clocks_and_summary_tables() -> None:
    """Clock parents and the three summary tables are populated."""
    conn, _ = parse_report(FIXTURE)

    clocks = {row["name"]: row for row in conn.execute("SELECT * FROM clock")}
    assert clocks["clk_a"]["parent"] is None
    assert clocks["clk_a"]["indent"] == 0
    assert clocks["clk_a"]["period_ns"] == pytest.approx(4.000)
    assert clocks["clk_a"]["freq_mhz"] == pytest.approx(250.000)
    assert clocks["clk_a_div2"]["parent"] == "clk_a"
    assert clocks["clk_a_div2"]["indent"] == 2
    assert clocks["clk_a_div2"]["period_ns"] == pytest.approx(8.000)
    assert clocks["clk_a_div2"]["freq_mhz"] == pytest.approx(125.000)

    kinds = {
        row["kind"]: row["n"]
        for row in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM timing_summary GROUP BY kind")
    }
    assert kinds == {"design": 1, "intra": 2, "inter": 1, "other": 1}
    design = conn.execute(
        "SELECT * FROM timing_summary WHERE kind = 'design'").fetchone()
    assert design["wns"] == pytest.approx(-1.234)
    assert design["tns"] == pytest.approx(-1.234)
    # Characterization: the design row stores tns_failing/tns_total under
    # their report names, so the generic endpoint columns stay NULL.
    assert design["failing_endpoints"] is None

    intra = conn.execute(
        "SELECT * FROM timing_summary WHERE kind = 'intra'"
        " ORDER BY path_group").fetchall()
    assert {row["path_group"] for row in intra} == {"clk_a", "clk_a_div2"}
    clk_a = next(row for row in intra if row["path_group"] == "clk_a")
    assert clk_a["wns"] == pytest.approx(-1.234)
    assert clk_a["wpws"] == pytest.approx(0.800)
    assert clk_a["tpws_failing"] == 0


def test_clock_pair_characterization() -> None:
    """Clock pair counts are parsed; current WNS/TNS extraction is broken."""
    conn, _ = parse_report(FIXTURE)
    pair = conn.execute("SELECT * FROM clock_pair").fetchone()
    assert pair["section_seq"] == 1
    assert pair["from_clock"] == "clk_a"
    assert pair["to_clock"] == "clk_a"
    assert pair["setup_failing"] == 1
    assert pair["pw_failing"] == 0
    assert pair["hold_failing"] is None
    # Characterization: RE_PAIR captures the "ns" suffix, so _fnum() returns
    # None.  Fixing that is a separate change, not part of this refactor.
    assert pair["setup_wns"] is None
    assert pair["setup_tns"] is None


# -- edge report ------------------------------------------------------------
def test_edge_report_hidden_endpoints_and_notes() -> None:
    """Hidden endpoints, CDC notes and timing exceptions are preserved."""
    conn, result = parse_report(EDGE_FIXTURE)
    assert result.counts == {"paths": 1, "segments": 7, "pw": 0}
    assert result.issues == []

    row = conn.execute("SELECT * FROM path").fetchone()
    assert row["source_pin"] == "<hidden>"
    assert row["dest_pin"] == "<hidden>"
    assert row["source_cell"] is None
    assert row["dest_cell"] is None
    assert row["source_cell_type"] == "FDRE"
    assert row["dest_cell_type"] == "FDRE"
    assert row["source_clock"] == "clk_x"
    assert row["dest_clock"] == "clk_y"
    assert row["src_hidden"] == 1
    assert row["dst_hidden"] == 1
    assert row["section_from_clock"] == "clk_x"
    assert row["section_to_clock"] == "clk_y"
    assert row["timing_exception"] == "MaxDelay Path 4.000ns -datapath_only"
    assert row["cdc_note"].startswith("Inter clock paths are considered valid")
    assert row["fanout_max"] == 3
    assert row["n_data_seg"] == 5
    assert row["required_ns"] - row["arrival_ns"] == pytest.approx(row["slack"])

    # Hidden endpoint names never become hierarchy prefixes.
    assert conn.execute("SELECT COUNT(*) FROM hier_prefix").fetchone()[0] == 0
    hidden = conn.execute(
        "SELECT hidden FROM resource WHERE name = '<hidden>'").fetchone()[0]
    assert hidden == 1
    assert conn.execute("SELECT COUNT(*) FROM import_issue").fetchone()[0] == 0


def test_timing_exception_skips_delay_mismatch(tmp_path: Path) -> None:
    """Exception paths may have data_delay != sum(incr) without an issue."""
    text = EDGE_FIXTURE.read_text(encoding="utf-8").replace(
        "Data Path Delay:        5.000ns",
        "Data Path Delay:        9.000ns",
    )
    conn, result = parse_text(tmp_path, text)
    assert result.issues == []
    row = conn.execute("SELECT * FROM path").fetchone()
    assert row["data_delay_ns"] == pytest.approx(9.000)
    assert row["timing_exception"] is not None


def test_delay_mismatch_without_exception(tmp_path: Path) -> None:
    """Without an exception, sum(incr) is checked against Data Path Delay."""
    text = EDGE_FIXTURE.read_text(encoding="utf-8")
    text = text.replace(
        "  Timing Exception:       MaxDelay Path 4.000ns -datapath_only\n", "")
    text = text.replace(
        "Data Path Delay:        5.000ns",
        "Data Path Delay:        9.000ns",
    )
    conn, result = parse_text(tmp_path, text)
    assert [issue[2] for issue in result.issues] == ["delay_mismatch"]
    assert "sum(incr)" in result.issues[0][3]
    assert conn.execute("SELECT COUNT(*) FROM import_issue").fetchone()[0] == 1


def test_slack_mismatch_is_reported(tmp_path: Path) -> None:
    """required - arrival must equal the printed slack."""
    text = EDGE_FIXTURE.read_text(encoding="utf-8").replace(
        "arrival time                          -5.000",
        "arrival time                          -4.000",
    )
    _, result = parse_text(tmp_path, text)
    assert [issue[2] for issue in result.issues] == ["slack_mismatch"]
    assert "required - arrival" in result.issues[0][3]

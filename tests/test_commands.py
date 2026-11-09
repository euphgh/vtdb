"""Tests for the command rendering helpers."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MINI = ROOT / "tests" / "data" / "mini_timing.rpt"
EDGE = ROOT / "tests" / "data" / "edge_timing.rpt"

sys.path.insert(0, str(SRC))

from vtdb.commands.path import path_summary_lines, shorten_name  # noqa: E402
from vtdb.db import DbWriter, create_memory_database  # noqa: E402
from vtdb.parser import ReportParser  # noqa: E402


def load_path(report: Path, seq: int = 1) -> dict:
    """Parse ``report`` in memory and return one path row.

    Args:
        report: Report fixture to parse.
        seq: Path sequence number.

    Returns:
        Path row as a dictionary.
    """
    conn = create_memory_database()
    conn.row_factory = sqlite3.Row
    writer = DbWriter(conn)
    ReportParser(report, writer).run()
    writer.close()
    return dict(conn.execute("SELECT * FROM path WHERE seq = ?", (seq,)).fetchone())


def test_path_summary_shows_clock_pair_and_cdc() -> None:
    """The summary shows the clock pair and wraps a CDC note."""
    lines = path_summary_lines(load_path(EDGE))
    assert "  clocks        clk_x -> clk_y" in lines

    cdc = [line for line in lines if line.startswith("  cdc")]
    assert cdc
    assert cdc[0].startswith("  cdc           Inter clock paths")
    block = lines[lines.index(cdc[0]):]
    assert len(block) > 1
    assert all(line.startswith(" " * 16) for line in block[1:])
    assert all(len(line) <= 76 for line in block)


def test_path_summary_uses_dashes_for_missing_clocks() -> None:
    """Missing clock names render as ``-`` and omit the CDC block."""
    path = load_path(EDGE)
    path["section_from_clock"] = None
    path["section_to_clock"] = None
    path["cdc_note"] = None

    lines = path_summary_lines(path)
    assert "  clocks        - -> -" in lines
    assert not any(line.startswith("  cdc") for line in lines)


def test_path_summary_without_cdc_note() -> None:
    """A path without a CDC note keeps the clocks line only."""
    lines = path_summary_lines(load_path(MINI))
    assert "  clocks        clk_a -> clk_a" in lines
    assert not any(line.startswith("  cdc") for line in lines)


LONG_PIN = (
    "xiangshan_i/u_role/inst/Part0FabricRouterWra_0/inst/router/core/"
    "gen_ingress[3].ingress/gen_direction[1].queue/head/out_data_reg[38]/C"
)


def test_shorten_name_keeps_short_and_hidden_names() -> None:
    """Short names and hidden placeholders are returned unchanged."""
    assert shorten_name("u_a/reg/C") == "u_a/reg/C"
    assert shorten_name("<hidden>") == "<hidden>"


def test_shorten_name_elides_middle_segments() -> None:
    """Long names keep the first two and last three segments."""
    result = shorten_name(LONG_PIN, limit=60)
    assert result.startswith("xiangshan_i/u_role/…/")
    assert result.endswith("head/out_data_reg[38]/C")
    assert len(result) <= 60


def test_shorten_name_truncates_when_there_are_no_middle_segments() -> None:
    """A long name with few segments still respects the limit."""
    result = shorten_name("a" * 50 + "/" + "b" * 50, limit=40)
    assert len(result) <= 40
    assert result.endswith("…")


def test_path_summary_shortens_endpoint_names_unless_wide() -> None:
    """Default summaries fit 80 columns; ``wide`` keeps the full pin name."""
    path = load_path(MINI)
    path["source_pin"] = LONG_PIN
    path["dest_pin"] = LONG_PIN

    default = path_summary_lines(path)
    source = next(line for line in default if line.startswith("  source"))
    destination = next(line for line in default if line.startswith("  destination"))
    assert len(source) <= 80
    assert len(destination) <= 80
    assert "…" in source

    wide = path_summary_lines(path, wide=True)
    assert any(LONG_PIN in line for line in wide)

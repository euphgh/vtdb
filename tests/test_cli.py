"""Tests for the vtdb command line interface."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "data" / "mini_timing.rpt"

sys.path.insert(0, str(SRC))


def run_cli(*args, cwd: Path | None = None, env_extra: dict[str, str] | None = None):
    """Run ``python -m vtdb`` in a subprocess.

    Args:
        *args: Command line arguments passed to the CLI.
        cwd: Working directory for the subprocess.
        env_extra: Extra environment variables.

    Returns:
        Completed process with captured stdout and stderr.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "vtdb", *map(str, args)],
        capture_output=True, text=True, env=env, cwd=cwd,
    )


def import_fixture(target: Path, *extra: str, cwd: Path | None = None):
    """Import the fixture report into ``target``.

    Args:
        target: Output database path.
        *extra: Additional ``import`` options.
        cwd: Working directory for the subprocess.

    Returns:
        Completed process.
    """
    return run_cli("import", FIXTURE, "--db", target, *extra, cwd=cwd)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Provide a database imported from the fixture report.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the created database.
    """
    path = tmp_path / "mini.db"
    result = import_fixture(path, "--quiet")
    assert result.returncode == 0, result.stderr
    return path


def query(db_path: Path, sql: str, params=()) -> list[sqlite3.Row]:
    """Run a SQL query against ``db_path``.

    Args:
        db_path: Database file.
        sql: Query text.
        params: Query parameters.

    Returns:
        Result rows.
    """
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def json_rows(result: subprocess.CompletedProcess) -> list[dict]:
    """Decode a CLI result that was produced with ``--format json``.

    Args:
        result: Completed process.

    Returns:
        Decoded JSON array.
    """
    return json.loads(result.stdout)


# -- import -----------------------------------------------------------------
def test_import_counts(db: Path) -> None:
    """Import stores the expected number of rows and metadata."""
    assert query(db, "SELECT COUNT(*) FROM path")[0][0] == 2
    assert query(db, "SELECT COUNT(*) FROM path_segment")[0][0] == 23
    assert query(db, "SELECT COUNT(*) FROM pw_check")[0][0] == 3
    assert query(db, "SELECT COUNT(*) FROM clock")[0][0] == 2
    row = query(db, "SELECT design_name, design_state, stage, constraints_met FROM report")[0]
    assert tuple(row) == ("mini_top", "Synthesized", "synth", 0)
    assert query(db, "SELECT COUNT(*) FROM import_issue")[0][0] == 0


def test_import_refuses_overwrite(tmp_path: Path) -> None:
    """Importing twice fails unless ``--force`` is given."""
    target = tmp_path / "mini.db"
    assert import_fixture(target, "--quiet").returncode == 0
    second = import_fixture(target, "--quiet")
    assert second.returncode == 3
    assert "exists" in second.stderr
    assert import_fixture(target, "--force", "--quiet").returncode == 0


def test_import_dry_run(tmp_path: Path) -> None:
    """``--dry-run`` parses without creating a database."""
    target = tmp_path / "dry.db"
    result = import_fixture(target, "--dry-run")
    assert result.returncode == 0
    assert "paths      2" in result.stdout
    assert not target.exists()


def test_import_json_summary(tmp_path: Path) -> None:
    """``import --format json`` emits a machine readable summary."""
    target = tmp_path / "mini.db"
    result = import_fixture(target, "--format", "json")
    payload = json.loads(result.stdout)
    assert payload["paths"] == 2
    assert payload["violated"] == 1
    assert payload["checks"] == "ok"


def test_keep_detail_none(tmp_path: Path) -> None:
    """``--keep-detail none`` drops detail rows and topology filters."""
    target = tmp_path / "none.db"
    assert import_fixture(target, "--keep-detail", "none", "--quiet").returncode == 0
    assert query(target, "SELECT COUNT(*) FROM path")[0][0] == 2
    assert query(target, "SELECT COUNT(*) FROM path_segment")[0][0] == 0
    result = run_cli("top", "--db", target, "--net", "u_a/n1")
    assert result.returncode == 2
    assert "keep-detail" in result.stderr


def test_keep_detail_negative(tmp_path: Path) -> None:
    """``--keep-detail negative`` keeps rows only for violated paths."""
    target = tmp_path / "neg.db"
    assert import_fixture(target, "--keep-detail", "negative", "--quiet").returncode == 0
    assert query(target, "SELECT COUNT(DISTINCT path_seq) FROM path_segment")[0][0] == 1


def test_no_hier_disables_module_stats(tmp_path: Path) -> None:
    """``--no-hier`` stores no prefixes and ``stats --by module`` fails."""
    target = tmp_path / "flat.db"
    assert import_fixture(target, "--no-hier", "--quiet").returncode == 0
    assert query(target, "SELECT COUNT(*) FROM hier_prefix")[0][0] == 0
    result = run_cli("stats", "--db", target, "--by", "module")
    assert result.returncode == 2
    assert "no-hier" in result.stderr


# -- top --------------------------------------------------------------------
def test_top_fail_only(db: Path) -> None:
    """``top --fail-only`` returns only violated paths."""
    rows = json_rows(run_cli("top", "--db", db, "--fail-only", "--format", "json"))
    assert len(rows) == 1
    assert rows[0]["seq"] == 1
    assert rows[0]["slack"] == pytest.approx(-1.234)


def test_top_sort_and_filter(db: Path) -> None:
    """Filters and sorting are applied server side."""
    rows = json_rows(run_cli("top", "--db", db, "--min-levels", "3", "--format", "json"))
    assert [row["seq"] for row in rows] == [1]


def test_top_net_filter(db: Path) -> None:
    """``--net`` keeps paths crossing a matching net."""
    rows = json_rows(run_cli("top", "--db", db, "--net", "u_a/n1", "--format", "json"))
    assert [row["seq"] for row in rows] == [1]
    assert json_rows(run_cli("top", "--db", db, "--net", "does_not_exist",
                             "--format", "json")) == []


def test_top_cell_and_site_filters(db: Path) -> None:
    """``--cell`` matches instances and ``--site`` exact sites."""
    assert [row["seq"] for row in json_rows(
        run_cli("top", "--db", db, "--cell", "reg_b_reg", "--format", "json"))] == [1]
    assert [row["seq"] for row in json_rows(
        run_cli("top", "--db", db, "--site", "SLICE_X5Y5", "--format", "json"))] == [1]


def test_top_csv_format(db: Path) -> None:
    """CSV output has a header and one row per path."""
    result = run_cli("top", "--db", db, "--format", "csv")
    lines = result.stdout.strip().splitlines()
    assert lines[0].startswith("#,SLACK")
    assert len(lines) == 3


# -- path -------------------------------------------------------------------
def test_path_summary_and_segments(db: Path) -> None:
    """``path`` prints the summary block and the detail table."""
    result = run_cli("path", "--db", db, "1")
    assert result.returncode == 0
    assert "VIOLATED" in result.stdout
    assert "launch_clock" in result.stdout
    assert "capture_clock" in result.stdout
    assert "u_a/n1" in result.stdout


def test_path_json(db: Path) -> None:
    """``path --format json`` returns the row and its segments."""
    payload = json.loads(run_cli("path", "--db", db, "1", "--format", "json").stdout)
    assert payload["path"]["seq"] == 1
    assert len(payload["segments"]) == 16
    assert payload["segments"][0]["block"] == "launch_clock"


def test_path_out_of_range(db: Path) -> None:
    """A missing sequence number is a usage error listing the valid range."""
    result = run_cli("path", "--db", db, "99")
    assert result.returncode == 2
    assert "1..2" in result.stderr


def test_path_raw(db: Path) -> None:
    """``--raw`` prints the original report lines of the path."""
    result = run_cli("path", "--db", db, "1", "--raw")
    assert result.returncode == 0
    assert "Slack (VIOLATED)" in result.stdout


# -- stats ------------------------------------------------------------------
def test_stats_group(db: Path) -> None:
    """``stats --by group`` aggregates per path group."""
    rows = json_rows(run_cli("stats", "--db", db, "--by", "group", "--format", "json"))
    assert {row["grp"] for row in rows} == {"clk_a", "clk_a_div2"}
    failing = next(row for row in rows if row["grp"] == "clk_a")
    assert failing["paths"] == 1
    assert failing["fail"] == 1
    assert failing["wns"] == pytest.approx(-1.234)


def test_stats_clock_pair(db: Path) -> None:
    """``stats --by clock-pair`` groups by section clocks."""
    rows = json_rows(run_cli("stats", "--db", db, "--by", "clock-pair",
                             "--format", "json"))
    assert rows[0]["from_clock"] == "clk_a"
    assert rows[0]["to_clock"] == "clk_a"


def test_stats_module_level(db: Path) -> None:
    """``stats --by module`` aggregates on hierarchy prefixes."""
    rows = json_rows(run_cli("stats", "--db", db, "--by", "module",
                             "--level", "1", "--format", "json"))
    assert [row["prefix"] for row in rows] == ["u_a", "u_c"]
    rows = json_rows(run_cli("stats", "--db", db, "--by", "module", "--level", "2",
                             "--side", "dest", "--format", "json"))
    assert {row["prefix"] for row in rows} == {"u_b/reg_b_reg[0]", "u_d/sink_reg[0]"}


def test_stats_module_filters(db: Path) -> None:
    """Module filters are applied before aggregation and combine with AND."""
    rows = json_rows(run_cli(
        "stats", "--db", db, "--by", "module", "--level", "1",
        "--fail-only", "--format", "json"))
    assert [(row["prefix"], row["paths"], row["fail"]) for row in rows] == [
        ("u_a", 1, 1)]

    rows = json_rows(run_cli(
        "stats", "--db", db, "--by", "module", "--level", "1",
        "--group", "div2", "--format", "json"))
    assert [row["prefix"] for row in rows] == ["u_c"]

    rows = json_rows(run_cli(
        "stats", "--db", db, "--by", "module", "--level", "1",
        "--prefix", "u_a", "--format", "json"))
    assert [row["prefix"] for row in rows] == ["u_a"]

    rows = json_rows(run_cli(
        "stats", "--db", db, "--by", "module", "--level", "1",
        "--group", "div2", "--prefix", "u_a", "--format", "json"))
    assert rows == []

    rows = json_rows(run_cli(
        "stats", "--db", db, "--by", "module", "--level", "1",
        "--fail-only", "--min-paths", "2", "--format", "json"))
    assert rows == []


def test_stats_module_filter_requires_module_mode(db: Path) -> None:
    """The module filters are rejected for group/clock-pair aggregation."""
    result = run_cli("stats", "--db", db, "--by", "group", "--fail-only")
    assert result.returncode == 2
    assert "require --by module" in result.stderr


# -- sql --------------------------------------------------------------------
def test_sql_select(db: Path) -> None:
    """Read-only queries return rows as JSON when requested."""
    rows = json_rows(run_cli("sql", "--db", db,
                             "SELECT seq, slack FROM path ORDER BY seq",
                             "--format", "json"))
    assert [row["seq"] for row in rows] == [1, 2]


def test_sql_rejects_writes(db: Path) -> None:
    """Non-query statements are rejected before reaching SQLite."""
    result = run_cli("sql", "--db", db, "DELETE FROM path")
    assert result.returncode == 2
    assert "SELECT" in result.stderr
    assert query(db, "SELECT COUNT(*) FROM path")[0][0] == 2


def test_sql_param(db: Path) -> None:
    """``--param`` binds values instead of interpolating them."""
    rows = json_rows(run_cli("sql", "--db", db,
                             "SELECT seq FROM path WHERE slack < :limit",
                             "--param", "limit=0", "--format", "json"))
    assert [row["seq"] for row in rows] == [1]


# -- database resolution ----------------------------------------------------
def test_db_from_cwd(tmp_path: Path, db: Path) -> None:
    """A single ``*.db`` in the working directory is used implicitly."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    shutil.copy(db, workdir / "cwd.db")
    result = run_cli("top", "--fail-only", "--format", "json", cwd=workdir)
    assert result.returncode == 0
    assert len(json_rows(result)) == 1


def test_db_from_env(tmp_path: Path, db: Path) -> None:
    """``VTD_DB`` selects the database when ``--db`` is absent."""
    result = run_cli("top", "--fail-only", "--format", "json",
                     env_extra={"VTD_DB": str(db)})
    assert result.returncode == 0


def test_db_ambiguous(tmp_path: Path, db: Path) -> None:
    """Two databases in the working directory require an explicit choice."""
    shutil.copy(db, tmp_path / "a.db")
    shutil.copy(db, tmp_path / "b.db")
    result = run_cli("top", cwd=tmp_path)
    assert result.returncode == 4
    assert "multiple databases" in result.stderr


def test_db_missing(tmp_path: Path) -> None:
    """A missing database is reported with exit code 4."""
    result = run_cli("top", "--db", tmp_path / "nope.db")
    assert result.returncode == 4
    assert "not found" in result.stderr


def test_version() -> None:
    """``--version`` prints the tool version."""
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.startswith("vtdb ")


# -- summary ----------------------------------------------------------------
def test_summary_json(db: Path) -> None:
    """``summary --format json`` returns the three documented sections."""
    payload = json.loads(run_cli(
        "summary", "--db", db, "--format", "json").stdout)
    assert set(payload) == {"metadata", "reported", "imported"}

    assert payload["metadata"]["design_name"] == "mini_top"
    assert payload["metadata"]["stage"] == "synth"

    setup = payload["reported"]["setup"]
    assert setup["wns"] == pytest.approx(-1.234)
    assert setup["tns"] == pytest.approx(-1.234)
    assert setup["failing_endpoints"] == 1
    assert setup["total_endpoints"] == 2
    assert payload["reported"]["hold"] == {
        "available": False, "reason": "hold summary not captured"}
    assert payload["reported"]["constraints_met"] is False
    assert payload["reported"]["pulse_width"]["wpws"] == pytest.approx(0.800)

    imported = payload["imported"]
    assert imported["paths"] == 2
    assert imported["paths_with_detail"] == 2
    assert imported["paths_without_detail"] == 0
    assert imported["paths_by_type"] == [
        {"path_type": "Setup", "keep_detail": 1, "count": 2}]
    assert imported["segments"] == 23
    assert imported["pw_checks"] == 3
    assert imported["delay_type"] == "max"
    assert imported["max_paths"] == 1000
    assert imported["issues"] == []


def test_summary_text(db: Path) -> None:
    """``summary`` prints grouped metadata, reported and imported sections."""
    result = run_cli("summary", "--db", db)
    assert result.returncode == 0
    assert "metadata" in result.stdout
    assert "reported" in result.stdout
    assert "imported" in result.stdout
    assert "hold" in result.stdout
    assert "unavailable" in result.stdout
    assert "not met" in result.stdout
    assert "Setup=2" in result.stdout
    assert "1000 (report option)" in result.stdout


def test_summary_keep_detail_none(tmp_path: Path) -> None:
    """``summary`` distinguishes detail omitted at import from missing data."""
    target = tmp_path / "none.db"
    assert import_fixture(target, "--keep-detail", "none", "--quiet").returncode == 0
    payload = json.loads(run_cli(
        "summary", "--db", target, "--format", "json").stdout)
    assert payload["imported"]["paths"] == 2
    assert payload["imported"]["paths_with_detail"] == 0
    assert payload["imported"]["paths_without_detail"] == 2
    assert payload["imported"]["segments"] == 0
    assert payload["imported"]["paths_by_type"] == [
        {"path_type": "Setup", "keep_detail": 0, "count": 2}]

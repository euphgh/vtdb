"""CLI contract tests: JSON keys, exit codes and the help surface.

The refactor must not change the published CLI contract.  These tests pin the
parts that are easy to break while moving command code around: JSON field
names, output headers, argument options and process exit codes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "data" / "mini_timing.rpt"

sys.path.insert(0, str(SRC))

from vtdb.db import PATH_COLUMNS  # noqa: E402


def run_cli(*args, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run ``python -m vtdb`` in a subprocess.

    Args:
        *args: Command line arguments passed to the CLI.
        cwd: Working directory for the subprocess.

    Returns:
        Completed process with captured stdout and stderr.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, "-m", "vtdb", *map(str, args)],
        capture_output=True, text=True, env=env, cwd=cwd,
    )


def json_rows(result: subprocess.CompletedProcess) -> list[dict]:
    """Decode a CLI result produced with ``--format json``.

    Args:
        result: Completed process.

    Returns:
        Decoded JSON array.
    """
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provide a database imported from the fixture report.

    Args:
        tmp_path_factory: Pytest temporary directory factory.

    Returns:
        Path to the created database.
    """
    path = tmp_path_factory.mktemp("contract") / "mini.db"
    result = run_cli("import", FIXTURE, "--db", path, "--quiet")
    assert result.returncode == 0, result.stderr
    return path


# -- JSON field names -------------------------------------------------------
def test_import_json_keys(tmp_path: Path) -> None:
    """``import --format json`` exposes the documented summary fields."""
    target = tmp_path / "import.db"
    result = run_cli("import", FIXTURE, "--db", target, "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "report", "database", "design", "state", "paths", "violated",
        "segments", "pw_checks", "checks", "issues", "elapsed_s", "size_bytes",
    }
    assert payload["checks"] == "ok"
    assert payload["issues"] == []


def test_top_json_keys(db: Path) -> None:
    """``top --format json`` keeps the exact column names."""
    rows = json_rows(run_cli("top", "--db", db, "--format", "json"))
    assert len(rows) == 2
    assert set(rows[0]) == {
        "seq", "slack", "path_group", "logic_levels", "route_pct",
        "source", "dest", "note",
    }


def test_path_json_keys(db: Path) -> None:
    """``path --format json`` contains every path column and segment field."""
    payload = json.loads(run_cli(
        "path", "--db", db, "1", "--format", "json").stdout)
    assert set(payload) == {"path", "segments"}
    assert set(payload["path"]) == set(PATH_COLUMNS)
    assert len(payload["segments"]) == 16
    assert set(payload["segments"][0]) == {
        "block", "seq", "site", "cell_type", "pin_func",
        "incr_ns", "cum_ns", "fanout", "resource",
    }


@pytest.mark.parametrize(
    ("by", "expected"),
    [
        ("group", {"grp", "paths", "fail", "wns", "tns_sample", "route_pct"}),
        ("clock-pair", {"from_clock", "to_clock", "paths", "fail", "wns",
                        "tns_sample", "route_pct"}),
        ("module", {"prefix", "paths", "fail", "wns", "tns_sample"}),
    ],
)
def test_stats_json_keys(db: Path, by: str, expected: set[str]) -> None:
    """Each ``stats --by`` mode keeps its documented column names."""
    rows = json_rows(run_cli(
        "stats", "--db", db, "--by", by, "--level", "1", "--format", "json"))
    assert rows
    assert set(rows[0]) == expected


def test_sql_json_keys(db: Path) -> None:
    """``sql --format json`` uses the query's column names as keys."""
    rows = json_rows(run_cli(
        "sql", "--db", db, "SELECT 1 AS one, 'x' AS two", "--format", "json"))
    assert rows == [{"one": 1, "two": "x"}]


# -- output and matching semantics -----------------------------------------
def test_top_csv_header(db: Path) -> None:
    """CSV output starts with the stable header row."""
    lines = run_cli("top", "--db", db, "--format", "csv").stdout.splitlines()
    assert lines[0] == "#,SLACK,GROUP,LVL,ROUTE%,SOURCE,DEST,NOTE"
    assert len(lines) == 3


def test_top_group_substring_and_exact(db: Path) -> None:
    """``--group`` is a substring match; ``--exact`` restores equality."""
    partial = json_rows(run_cli(
        "top", "--db", db, "--group", "clk", "--format", "json"))
    assert [row["seq"] for row in partial] == [1, 2]

    no_partial = json_rows(run_cli(
        "top", "--db", db, "--group", "clk", "--exact", "--format", "json"))
    assert no_partial == []

    exact = json_rows(run_cli(
        "top", "--db", db, "--group", "clk_a", "--exact", "--format", "json"))
    assert [row["seq"] for row in exact] == [1]


def test_empty_result_is_success(db: Path) -> None:
    """An empty result is not an error and announces the filter on stderr."""
    result = run_cli(
        "top", "--db", db, "--group", "does_not_exist", "--format", "json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == []
    assert "no rows (group~'does_not_exist')" in result.stderr

    combined = run_cli(
        "top", "--db", db, "--group", "does_not_exist", "--fail-only",
        "--format", "json")
    assert "slack<0 AND group~'does_not_exist'" in combined.stderr

    exact = run_cli(
        "top", "--db", db, "--group", "clk", "--exact", "--format", "json")
    assert "no rows (group='clk')" in exact.stderr


# -- argument surface -------------------------------------------------------
@pytest.mark.parametrize(
    ("command", "options"),
    [
        ("import", ["--keep-detail", "--dry-run", "--strict", "--force"]),
        ("top", ["--fail-only", "--group", "--exact", "--net", "--cell",
                 "--site", "--sort", "--wide"]),
        ("path", ["--no-segments", "--raw"]),
        ("stats", ["--by", "--level", "--side", "--fail-only", "--group",
                   "--prefix", "--min-paths", "--sort"]),
        ("sql", ["--file", "--param", "--explain"]),
    ],
)
def test_help_lists_options(command: str, options: list[str]) -> None:
    """Every documented option remains in ``--help``."""
    result = run_cli(command, "--help")
    assert result.returncode == 0
    for option in options:
        assert option in result.stdout


# -- exit codes -------------------------------------------------------------
def test_usage_errors_exit_2(db: Path) -> None:
    """Bad arguments and write attempts are usage errors."""
    assert run_cli("path", "--db", db, "99").returncode == 2
    assert run_cli("sql", "--db", db, "DELETE FROM path").returncode == 2
    assert run_cli("no_such_command").returncode == 2


def test_missing_database_exits_4(tmp_path: Path) -> None:
    """A missing database is a database error, not a usage error."""
    result = run_cli("top", "--db", tmp_path / "missing.db")
    assert result.returncode == 4
    assert "not found" in result.stderr

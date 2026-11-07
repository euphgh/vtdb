"""SQLite persistence for parsed Vivado timing reports.

One database file describes exactly one report, so no table carries a
``report_id``.  The writer streams rows into small batches instead of keeping
the whole report in memory: a 200 MB report produces roughly 600k detail rows.
"""

from __future__ import annotations

import hashlib
import sqlite3
from importlib.resources import files
from pathlib import Path

SCHEMA_VERSION = 1

BATCH_SIZE = 20000

PATH_COLUMNS = (
    "seq", "line_start", "line_end", "section_seq", "section_from_clock",
    "section_to_clock", "path_group", "path_type", "max_min", "corner",
    "slack", "slack_status", "requirement_ns", "requirement_text",
    "data_delay_ns", "logic_ns", "route_ns", "logic_pct", "route_pct",
    "logic_levels", "logic_breakdown", "skew_ns", "dcd_ns", "scd_ns", "cpr_ns",
    "uncertainty_ns", "tsj_ns", "tij_ns", "dj_ns", "pe_ns",
    "clk_net_src_ns", "clk_net_src_route_ns", "clk_net_src_dist_ns",
    "clk_net_dst_ns", "clk_net_dst_route_ns", "clk_net_dst_dist_ns",
    "source_pin", "source_cell", "source_cell_type", "source_clock",
    "dest_pin", "dest_cell", "dest_cell_type", "dest_clock",
    "src_hidden", "dst_hidden", "src_site", "dst_site",
    "timing_exception", "cdc_note", "arrival_ns", "required_ns",
    "path_key", "fanout_max", "n_seg", "n_data_seg", "keep_detail",
)

SEGMENT_COLUMNS = (
    "path_seq", "seq", "block", "is_net", "site", "cell_type", "pin_func",
    "delay_type", "fanout", "incr_ns", "cum_ns", "transition", "pblock",
    "resource_id",
)

REPORT_COLUMNS = (
    "singleton", "source_file", "file_sha256", "file_size", "stage",
    "tool_version", "run_date", "host", "command", "design_name", "device",
    "speed_file", "design_state", "temp_grade", "delay_type", "max_paths",
    "wns", "tns", "tns_failing", "tns_total", "wpws", "tpws", "tpws_failing",
    "tpws_total", "constraints_met", "n_paths", "n_segments", "n_pw_checks",
)


def schema_sql() -> str:
    """Return the bundled SQLite schema.

    The schema is read through :mod:`importlib.resources` so that the tool also
    works from a zipapp or a PyInstaller bundle, where ``__file__`` does not
    point at a regular file.

    Returns:
        Schema SQL text.
    """
    return files("vtdb").joinpath("schema.sql").read_text(encoding="utf-8")


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of ``path`` (used for import idempotency)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_database(path: Path) -> sqlite3.Connection:
    """Create a new database file and apply the schema."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.executescript(schema_sql())
    conn.commit()
    return conn


def create_memory_database() -> sqlite3.Connection:
    """Create an in-memory database (used by ``import --dry-run``)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema_sql())
    return conn


def open_database(path: Path) -> sqlite3.Connection:
    """Open an existing database read-only and verify the schema version."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"database schema version {version} does not match vtdb {SCHEMA_VERSION}"
        )
    return conn


class DbWriter:
    """Stream parsed rows into SQLite in batched transactions."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialise the writer for one database connection.

        Args:
            conn: Open SQLite connection with the schema applied.
        """
        self.conn = conn
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA journal_mode=OFF")
        self._buffers: dict[str, list[tuple]] = {}
        self._resources: dict[str, int] = {}
        self._resource_rows: list[tuple] = []
        self._issue_id = 0
        self._hier_ids: dict[str, int] = {}
        self._hier_rows: list[tuple] = []
        self.counts = {"paths": 0, "segments": 0, "pw": 0}

    # -- resource dimension -------------------------------------------------
    def resource_id(self, name: str | None) -> int | None:
        """Return the interned id for a pin/net name (``None`` stays ``None``)."""
        if not name:
            return None
        rid = self._resources.get(name)
        if rid is None:
            rid = len(self._resources) + 1
            self._resources[name] = rid
            leaf = name.rstrip("/").rsplit("/", 1)[-1]
            self._resource_rows.append((rid, name, leaf, int(name == "<hidden>")))
        return rid

    # -- generic insert -----------------------------------------------------
    def _add(self, table: str, columns: tuple[str, ...], row: tuple) -> None:
        """Queue one row and flush the batch when it is full.

        Args:
            table: Target table name.
            columns: Column names in insert order.
            row: Value tuple matching ``columns``.
        """
        buffer = self._buffers.setdefault(table, [])
        buffer.append(row)
        if len(buffer) >= BATCH_SIZE:
            self._flush_table(table, columns)

    def _flush_table(self, table: str, columns: tuple[str, ...]) -> None:
        """Insert the buffered rows of one table.

        Args:
            table: Table name.
            columns: Column names in insert order.
        """
        rows = self._buffers.get(table)
        if not rows:
            return
        placeholders = ",".join("?" * len(columns))
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
        self.conn.executemany(sql, rows)
        rows.clear()

    def flush(self) -> None:
        """Flush the path and path segment buffers.
        """
        for table, columns in (
            ("path", PATH_COLUMNS),
            ("path_segment", SEGMENT_COLUMNS),
        ):
            self._flush_table(table, columns)

    # -- report-level rows --------------------------------------------------
    def add_report(self, values: dict) -> None:
        """Insert the single ``report`` row.

        Args:
            values: Mapping of column name to value.
        """
        row = tuple(values.get(column) for column in REPORT_COLUMNS)
        self._add("report", REPORT_COLUMNS, row)

    def add_timer_setting(self, key: str, value: str) -> None:
        """Record one timer setting.

        Args:
            key: Setting name.
            value: Setting value as printed in the report.
        """
        self._add("timer_setting", ("key", "value"), (key, value))

    def add_check_timing(self, seq: int, category: str, count: int | None,
                         severity: str | None, detail: str | None) -> None:
        """Record one ``check_timing`` category.

        Args:
            seq: Category index in the report.
            category: Category name such as ``no_clock``.
            count: Reported object count.
            severity: ``HIGH``, ``MEDIUM``, ``LOW`` or ``None``.
            detail: Concatenated detail lines.
        """
        self._add("check_timing", ("seq", "category", "count", "severity", "detail"),
                  (seq, category, count, severity, detail))

    def add_clock(self, name: str, parent: str | None, indent: int,
                  waveform: str | None, period_ns: float | None,
                  freq_mhz: float | None) -> None:
        """Record one clock definition.

        Args:
            name: Clock name.
            parent: Parent clock inferred from indentation.
            indent: Indentation depth in the Clock Summary table.
            waveform: Waveform string such as ``{0.000 2.000}``.
            period_ns: Clock period in nanoseconds.
            freq_mhz: Clock frequency in megahertz.
        """
        self._add("clock", ("name", "parent", "indent", "waveform", "period_ns", "freq_mhz"),
                  (name, parent, indent, waveform, period_ns, freq_mhz))

    def add_summary(self, row: dict) -> None:
        """Record one row of a timing summary table.

        Args:
            row: Mapping with ``kind`` and the summary columns.
        """
        columns = ("kind", "path_group", "from_clock", "to_clock", "wns", "tns",
                   "failing_endpoints", "total_endpoints", "wpws", "tpws",
                   "tpws_failing", "tpws_total")
        self._add("timing_summary", columns,
                  tuple(row.get(column) for column in columns))

    def add_clock_pair(self, row: dict) -> None:
        """Record one clock pair section summary.

        Args:
            row: Mapping with Setup/Hold/PW aggregates of the section.
        """
        columns = ("section_seq", "from_clock", "to_clock", "setup_failing",
                   "setup_wns", "setup_tns", "hold_failing", "hold_wns",
                   "hold_tns", "pw_failing", "pw_wns", "pw_tns")
        self._add("clock_pair", columns,
                  tuple(row.get(column) for column in columns))

    # -- paths --------------------------------------------------------------
    def add_path(self, row: dict) -> None:
        """Record one path summary row.

        Args:
            row: Mapping with the path columns.
        """
        self.counts["paths"] += 1
        self._add("path", PATH_COLUMNS,
                  tuple(row.get(column) for column in PATH_COLUMNS))

    def add_segment(self, row: dict) -> None:
        """Record one detail row of a path.

        Args:
            row: Mapping with the ``path_segment`` columns.
        """
        self.counts["segments"] += 1
        self._add("path_segment", SEGMENT_COLUMNS,
                  tuple(row.get(column) for column in SEGMENT_COLUMNS))

    def add_logic_cell(self, path_seq: int, cell_type: str, count: int) -> None:
        """Record one cell type count of a path.

        Args:
            path_seq: Path sequence number.
            cell_type: Cell type such as ``CARRY8``.
            count: Number of instances on the path.
        """
        self._add("logic_cell", ("path_seq", "cell_type", "count"),
                  (path_seq, cell_type, count))

    def add_hier(self, path_seq: int, side: str, level: int, prefix: str) -> None:
        """Record one hierarchy prefix of a path endpoint.

        Prefixes repeat across paths, so they are interned into
        ``hier_node`` instead of being stored once per path.

        Args:
            path_seq: Path sequence number.
            side: ``source`` or ``dest``.
            level: One-based hierarchy depth.
            prefix: Slash separated prefix.
        """
        prefix_id = self._hier_ids.get(prefix)
        if prefix_id is None:
            prefix_id = len(self._hier_ids) + 1
            self._hier_ids[prefix] = prefix_id
            self._hier_rows.append((prefix_id, prefix))
        self._add("hier_prefix", ("path_seq", "side", "level", "prefix_id"),
                  (path_seq, side, level, prefix_id))

    def add_pw_check(self, row: dict) -> None:
        """Record one pulse width check row.

        Args:
            row: Mapping with the ``pw_check`` columns.
        """
        self.counts["pw"] += 1
        columns = ("id", "seq", "section_seq", "clock_name", "check_type", "corner",
                   "lib_pin", "ref_pin", "required_ns", "actual_ns", "slack_ns",
                   "site", "pin_id")
        self._add("pw_check", columns,
                  tuple(row.get(column) for column in columns))

    def add_issue(self, line_no: int | None, severity: str, code: str,
                  message: str, raw: str | None) -> None:
        """Record one import problem.

        Args:
            line_no: Report line the problem was detected on.
            severity: ``warning`` or ``error``.
            code: Stable problem code.
            message: Human readable description.
            raw: Raw report line, when available.
        """
        self._issue_id += 1
        self._add("import_issue", ("id", "line_no", "severity", "code", "message", "raw"),
                  (self._issue_id, line_no, severity, code, message, raw))

    # -- finish -------------------------------------------------------------
    def close(self) -> None:
        """Flush every buffer and the resource dimension table."""
        self.flush()
        self._flush_table("timer_setting", ("key", "value"))
        self._flush_table("check_timing", ("seq", "category", "count", "severity", "detail"))
        self._flush_table("clock", ("name", "parent", "indent", "waveform", "period_ns", "freq_mhz"))
        self._flush_table("timing_summary", ("kind", "path_group", "from_clock", "to_clock",
                                             "wns", "tns", "failing_endpoints", "total_endpoints",
                                             "wpws", "tpws", "tpws_failing", "tpws_total"))
        self._flush_table("clock_pair", ("section_seq", "from_clock", "to_clock", "setup_failing",
                                         "setup_wns", "setup_tns", "hold_failing", "hold_wns",
                                         "hold_tns", "pw_failing", "pw_wns", "pw_tns"))
        self._flush_table("logic_cell", ("path_seq", "cell_type", "count"))
        self._flush_table("hier_prefix", ("path_seq", "side", "level", "prefix_id"))
        self._flush_table("pw_check", ("id", "seq", "section_seq", "clock_name", "check_type",
                                       "corner", "lib_pin", "ref_pin", "required_ns", "actual_ns",
                                       "slack_ns", "site", "pin_id"))
        self._flush_table("import_issue", ("id", "line_no", "severity", "code", "message", "raw"))
        self.conn.executemany(
            "INSERT INTO hier_node (id, prefix) VALUES (?,?)",
            self._hier_rows,
        )
        self._hier_rows.clear()
        self.conn.executemany(
            "INSERT INTO resource (id, name, leaf, hidden) VALUES (?,?,?,?)",
            self._resource_rows,
        )
        self._resource_rows.clear()
        self.conn.executemany(
            "INSERT INTO report (" + ",".join(REPORT_COLUMNS) + ") VALUES ("
            + ",".join("?" * len(REPORT_COLUMNS)) + ")",
            self._buffers.pop("report", []),
        )
        self.conn.commit()

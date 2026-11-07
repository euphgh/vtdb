"""SQL construction for the vtdb query commands."""

from __future__ import annotations

SORT_TOP = {
    "slack": "p.slack ASC",
    "route": "p.route_pct DESC",
    "logic": "p.logic_levels DESC",
    "fanout": "p.fanout_max DESC",
}

SORT_STATS = {
    "wns": "wns ASC",
    "tns": "tns_sample ASC",
    "fail": "fail DESC",
    "paths": "paths DESC",
}


def top_sql(filters: dict, sort: str, wide: bool, limit: int) -> tuple[str, list]:
    """Build the ``top`` query."""
    where: list[str] = []
    params: list[object] = []

    if filters.get("fail_only"):
        where.append("p.slack < 0")
    if filters.get("max_slack") is not None:
        where.append("p.slack < ?")
        params.append(filters["max_slack"])
    if filters.get("group"):
        where.append("p.path_group = ?")
        params.append(filters["group"])
    if filters.get("type"):
        where.append("p.path_type = ?")
        params.append(filters["type"])
    if filters.get("min_levels") is not None:
        where.append("p.logic_levels >= ?")
        params.append(filters["min_levels"])
    if filters.get("net"):
        where.append(
            "EXISTS (SELECT 1 FROM path_segment s JOIN resource r ON r.id = s.resource_id"
            " WHERE s.path_seq = p.seq AND s.is_net = 1 AND r.name LIKE ?)"
        )
        params.append(f"%{filters['net']}%")
    if filters.get("cell"):
        where.append(
            "EXISTS (SELECT 1 FROM path_segment s JOIN resource r ON r.id = s.resource_id"
            " WHERE s.path_seq = p.seq AND s.is_net = 0"
            " AND (r.name LIKE ? OR s.cell_type LIKE ?))"
        )
        params.extend([f"%{filters['cell']}%", f"%{filters['cell']}%"])
    if filters.get("site"):
        where.append(
            "EXISTS (SELECT 1 FROM path_segment s"
            " WHERE s.path_seq = p.seq AND s.site = ?)"
        )
        params.append(filters["site"])

    if wide:
        source, dest = "p.source_pin", "p.dest_pin"
    else:
        source = "COALESCE(p.source_cell, CASE WHEN p.src_hidden = 1 THEN '<hidden>' END)"
        dest = "COALESCE(p.dest_cell, CASE WHEN p.dst_hidden = 1 THEN '<hidden>' END)"
    sql = (
        "SELECT p.seq AS seq, p.slack AS slack, p.path_group AS path_group,"
        " p.logic_levels AS logic_levels, p.route_pct AS route_pct,"
        f" {source} AS source, {dest} AS dest,"
        " CASE WHEN p.timing_exception IS NOT NULL THEN 'exc' ELSE '' END AS note"
        " FROM path p"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {SORT_TOP.get(sort, SORT_TOP['slack'])} LIMIT ?"
    params.append(limit)
    return sql, params


def stats_group_sql(sort: str, limit: int) -> tuple[str, list]:
    """Build the ``stats --by group`` query.

    Args:
        sort: Sort key name.
        limit: Row limit.

    Returns:
        SQL text and parameters.
    """
    sql = (
        "SELECT path_group AS grp, COUNT(*) AS paths, SUM(slack < 0) AS fail,"
        " ROUND(MIN(slack), 3) AS wns,"
        " ROUND(SUM(CASE WHEN slack < 0 THEN slack END), 3) AS tns_sample,"
        " ROUND(AVG(route_pct), 1) AS route_pct"
        " FROM path GROUP BY path_group"
        f" ORDER BY {SORT_STATS.get(sort, SORT_STATS['wns'])} LIMIT ?"
    )
    return sql, [limit]


def stats_clock_pair_sql(sort: str, limit: int) -> tuple[str, list]:
    """Build the ``stats --by clock-pair`` query.

    Args:
        sort: Sort key name.
        limit: Row limit.

    Returns:
        SQL text and parameters.
    """
    sql = (
        "SELECT section_from_clock AS from_clock, section_to_clock AS to_clock,"
        " COUNT(*) AS paths, SUM(slack < 0) AS fail,"
        " ROUND(MIN(slack), 3) AS wns,"
        " ROUND(SUM(CASE WHEN slack < 0 THEN slack END), 3) AS tns_sample,"
        " ROUND(AVG(route_pct), 1) AS route_pct"
        " FROM path GROUP BY section_from_clock, section_to_clock"
        f" ORDER BY {SORT_STATS.get(sort, SORT_STATS['wns'])} LIMIT ?"
    )
    return sql, [limit]


def stats_module_sql(level: int, side: str, min_paths: int, sort: str,
                     limit: int) -> tuple[str, list]:
    """Build the ``stats --by module`` query.

    Args:
        level: Hierarchy depth to aggregate on.
        side: ``source``, ``dest`` or ``both``.
        min_paths: Minimum number of paths per prefix.
        sort: Sort key name.
        limit: Row limit.

    Returns:
        SQL text and parameters.
    """
    where = ["h.level = ?"]
    params: list[object] = [level]
    if side != "both":
        where.append("h.side = ?")
        params.append(side)
    sql = (
        "SELECT n.prefix AS prefix, COUNT(*) AS paths, SUM(p.slack < 0) AS fail,"
        " ROUND(MIN(p.slack), 3) AS wns,"
        " ROUND(SUM(CASE WHEN p.slack < 0 THEN p.slack END), 3) AS tns_sample"
        " FROM path p JOIN hier_prefix h ON h.path_seq = p.seq"
        " JOIN hier_node n ON n.id = h.prefix_id"
        " WHERE " + " AND ".join(where) +
        " GROUP BY n.prefix"
        f" HAVING COUNT(*) >= {int(min_paths)}"
        f" ORDER BY {SORT_STATS.get(sort, SORT_STATS['wns'])} LIMIT ?"
    )
    params.append(limit)
    return sql, params


def path_row_sql(seq: int) -> tuple[str, list]:
    """Build the query for one path summary row.

    Args:
        seq: Path sequence number.

    Returns:
        SQL text and parameters.
    """
    return "SELECT * FROM path WHERE seq = ?", [seq]


def path_segments_sql(seq: int) -> tuple[str, list]:
    """Build the query for the detail rows of one path.

    Args:
        seq: Path sequence number.

    Returns:
        SQL text and parameters.
    """
    return (
        "SELECT s.block AS block, s.seq AS seq, s.site AS site,"
        " s.cell_type AS cell_type, s.pin_func AS pin_func,"
        " s.incr_ns AS incr_ns, s.cum_ns AS cum_ns, s.fanout AS fanout,"
        " r.name AS resource"
        " FROM path_segment s LEFT JOIN resource r ON r.id = s.resource_id"
        " WHERE s.path_seq = ? ORDER BY s.seq",
        [seq],
    )

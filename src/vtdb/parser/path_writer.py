"""Path finalisation, derived fields and consistency checks."""

from __future__ import annotations

from ..db import DbWriter
from .lexer import RE_LEVEL_ITEM
from .model import PathStats


def cell_name(pin: str | None) -> str | None:
    """Return the leaf cell name of a pin path.

    Args:
        pin: Full pin name such as ``a/b/reg[2]/C``.

    Returns:
        Cell name such as ``reg[2]``, or ``None`` for hidden names.
    """
    if not pin or pin == "<hidden>":
        return None
    head, _, _ = pin.rpartition("/")
    return head.rsplit("/", 1)[-1] if head else None


def should_store(keep_detail: str, path: dict) -> bool:
    """Return whether detail rows of a path are stored.

    Args:
        keep_detail: ``all``, ``negative`` or ``none``.
        path: Path row being built.

    Returns:
        ``True`` when the current policy keeps this path's detail rows.
    """
    if keep_detail == "all":
        return True
    if keep_detail == "negative":
        slack = path.get("slack")
        return slack is not None and slack < 0
    return False


def add_hierarchy(writer: DbWriter, seq: int, side: str, pin: str | None) -> None:
    """Emit hierarchy prefixes of one endpoint.

    Args:
        writer: Destination for hierarchy rows.
        seq: Path sequence number.
        side: ``source`` or ``dest``.
        pin: Full pin name.
    """
    if not pin or pin == "<hidden>":
        return
    parts = [part for part in pin.split("/") if part]
    for level, _ in enumerate(parts[:-1], start=1):
        writer.add_hier(seq, side, level, "/".join(parts[:level]))


def record_issue(writer: DbWriter, issues: list, lineno: int | None,
                 severity: str, code: str, message: str) -> None:
    """Record one import problem.

    Args:
        writer: Destination for the ``import_issue`` row.
        issues: Shared list receiving ``(line, severity, code, message)``.
        lineno: Report line the problem was detected on.
        severity: ``warning`` or ``error``.
        code: Stable problem code.
        message: Human readable description.
    """
    issues.append((lineno, severity, code, message))
    writer.add_issue(lineno, severity, code, message, None)


def check_path(writer: DbWriter, path: dict, stats: PathStats, issues: list) -> None:
    """Run consistency checks on one finished path.

    Args:
        writer: Destination for ``import_issue`` rows.
        path: Path row being written.
        stats: Accumulated segment statistics of the path.
        issues: Shared list receiving ``(line, severity, code, message)``.
    """
    required = path.get("required_ns")
    arrival = path.get("arrival_ns")
    slack = path.get("slack")
    if required is not None and arrival is not None and slack is not None:
        if abs(required - arrival - slack) > 0.002:
            record_issue(
                writer, issues, path["line_start"], "invariant", "slack_mismatch",
                f"required - arrival = {required - arrival:.3f}, slack = {slack:.3f}")
    if path.get("timing_exception") or path.get("data_delay_ns") is None:
        return
    total = stats.sum_data
    tolerance = 0.001 * stats.n_data + 0.002
    if abs(total - float(path["data_delay_ns"])) > tolerance:
        record_issue(
            writer, issues, path["line_start"], "warning", "delay_mismatch",
            f"sum(incr)={total:.3f} != data_delay={float(path['data_delay_ns']):.3f}")


def finish_path(writer: DbWriter, path: dict, stats: PathStats, lineno: int,
                keep_detail: str, hier: bool, issues: list) -> None:
    """Finish one path and write its rows.

    Args:
        writer: Destination for path, logic and hierarchy rows.
        path: Path row being written.
        stats: Accumulated segment statistics of the path.
        lineno: Line number of the closing ``slack`` row.
        keep_detail: ``all``, ``negative`` or ``none``.
        hier: Whether to emit hierarchy prefixes.
        issues: Shared list receiving ``(line, severity, code, message)``.
    """
    path["line_end"] = lineno
    path["n_seg"] = stats.n_seg
    path["n_data_seg"] = stats.n_data
    path["fanout_max"] = stats.fanout
    path["src_site"] = stats.src_site
    path["dst_site"] = stats.dst_site
    path["keep_detail"] = int(should_store(keep_detail, path))

    source = path.get("source_pin")
    dest = path.get("dest_pin")
    path["source_cell"] = cell_name(source)
    path["dest_cell"] = cell_name(dest)
    path["src_hidden"] = int(source == "<hidden>")
    path["dst_hidden"] = int(dest == "<hidden>")
    path["path_key"] = "|".join(str(part) for part in (
        source, dest, path.get("path_group"), path.get("path_type"), path.get("max_min")))

    writer.add_path(path)
    if path.get("logic_breakdown"):
        for cell_type, count in RE_LEVEL_ITEM.findall(str(path["logic_breakdown"])):
            writer.add_logic_cell(path["seq"], cell_type, int(count))
    if hier:
        add_hierarchy(writer, int(path["seq"]), "source", source)
        add_hierarchy(writer, int(path["seq"]), "dest", dest)
    check_path(writer, path, stats, issues)

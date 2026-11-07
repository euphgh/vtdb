"""Data model shared by the parser and its callers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParseResult:
    """Summary of one parsed report.

    Attributes:
        meta: Header metadata such as ``Design`` and ``Design State``.
        counts: Row counts for paths, segments and pulse width checks.
        issues: ``(line, severity, code, message)`` tuples for import problems.
        design: Design Timing Summary values.
    """

    meta: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    issues: list[tuple[int | None, str, str, str]] = field(default_factory=list)
    design: dict[str, float | int | None] = field(default_factory=dict)


@dataclass
class PathStats:
    """Per-path statistics accumulated from detail rows.

    Attributes:
        sum_data: Sum of the ``data`` block increments.
        fanout: Largest net fanout seen on the path.
        src_site: Site of the last launch-clock cell row.
        dst_site: Site of the first capture-clock cell row.
        n_seg: Number of detail rows on the path.
        n_data: Number of ``data`` rows with a delay increment.
    """

    sum_data: float = 0.0
    fanout: int | None = None
    src_site: str | None = None
    dst_site: str | None = None
    n_seg: int = 0
    n_data: int = 0


@dataclass
class CheckTimingRow:
    """One ``check_timing`` category.

    Attributes:
        seq: Category index in the report.
        category: Category name such as ``no_clock``.
        count: Reported object count.
        severity: ``HIGH``, ``MEDIUM``, ``LOW`` or ``None``.
        detail: Concatenated detail lines.
    """

    seq: int
    category: str
    count: int
    severity: str | None = None
    detail: str = ""

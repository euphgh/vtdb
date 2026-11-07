"""Streaming parser package for Vivado timing reports."""

from __future__ import annotations

from .core import ReportParser
from .lexer import split_delay_type, split_prefix
from .model import ParseResult
from .segments import split_segment

__all__ = [
    "ParseResult",
    "ReportParser",
    "split_delay_type",
    "split_prefix",
    "split_segment",
]

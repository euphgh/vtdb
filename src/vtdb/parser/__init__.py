"""Streaming parser package for Vivado timing reports."""

from __future__ import annotations

from .core import ReportParser
from .lexer import split_delay_type, split_prefix
from .model import ParseResult

__all__ = ["ParseResult", "ReportParser", "split_delay_type", "split_prefix"]

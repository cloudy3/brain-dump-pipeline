import argparse
from datetime import timedelta

import pytest

from app.core.time import SINGAPORE_TIMEZONE
from app.tools.preview_review import build_parser, parse_reference_time


def test_preview_parser_requires_a_supported_window() -> None:
    args = build_parser().parse_args(["--window", "evening"])
    assert args.window == "evening"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--window", "surprise"])


def test_preview_reference_timestamp_requires_an_offset() -> None:
    parsed = parse_reference_time("2026-08-22T19:00:00+08:00")
    assert parsed.utcoffset() == timedelta(hours=8)
    with pytest.raises(argparse.ArgumentTypeError, match="UTC offset"):
        parse_reference_time("2026-08-22T19:00:00")


def test_preview_default_clock_is_aware_singapore_time() -> None:
    parsed = parse_reference_time(None)
    assert parsed.tzinfo is SINGAPORE_TIMEZONE

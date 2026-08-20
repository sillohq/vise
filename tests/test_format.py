"""Rendering numbers a person is going to read forty of."""

from __future__ import annotations

import pytest

from sillo_vise.logs.format import duration, elapsed, number, size, truncate


class TestDuration:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.4, "400µs"),
            (38.0, "38ms"),
            (204.6, "205ms"),
            (2104.5, "2.10s"),
            (95_000.0, "1m 35s"),
        ],
    )
    def test_renders(self, value, expected):
        assert duration(value) == expected


class TestElapsed:
    @pytest.mark.parametrize(
        "value,expected",
        [(11.0, "11s"), (131.0, "2m 11s"), (67_200.0, "18h 40m"), (353_000.0, "4d 02h")],
    )
    def test_renders(self, value, expected):
        assert elapsed(value) == expected


class TestSize:
    @pytest.mark.parametrize(
        "value,expected",
        [(0, "0 B"), (840, "840 B"), (12_400, "12.4 kB"), (1_800_000_000, "1.8 GB")],
    )
    def test_renders(self, value, expected):
        assert size(value) == expected


class TestNumber:
    @pytest.mark.parametrize(
        "value,expected", [(5, "5"), (12_481, "12,481"), (6.4, "6.4"), (84_102, "84,102")]
    )
    def test_renders(self, value, expected):
        assert number(value) == expected


class TestTruncate:
    def test_short_text_is_untouched(self):
        assert truncate("/api/v1", 20) == "/api/v1"

    def test_long_text_is_marked(self):
        assert truncate("/api/v1/workspaces/3/search", 12).endswith("…")

    def test_the_result_is_exactly_the_width(self):
        assert len(truncate("/api/v1/workspaces/3/search", 12)) == 12

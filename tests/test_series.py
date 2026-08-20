"""The time series: bucketing, gaps, percentiles and flat memory."""

from __future__ import annotations

import time

from sillo_vise.recorder import Series, SeriesSet
from sillo_vise.recorder.series import RESERVOIR


def minutes_ago(count: int) -> float:
    return time.time() - count * 60


class TestBucketing:
    def test_observations_in_one_minute_share_a_bucket(self):
        series = Series("x")
        for _ in range(5):
            series.add(1.0)
        assert len(series) == 1

    def test_observations_in_different_minutes_do_not(self):
        series = Series("x")
        series.add(1.0, at=minutes_ago(2))
        series.add(1.0)
        assert len(series) >= 2

    def test_a_quiet_minute_is_kept_as_a_quiet_minute(self):
        series = Series("x")
        series.add(1.0, at=minutes_ago(3))
        series.add(1.0)
        assert 0 in series.counts(4)

    def test_the_window_is_a_ceiling(self):
        series = Series("x", window=5)
        for index in range(60):
            series.add(1.0, at=minutes_ago(index))
        assert len(series) <= 5


class TestPadding:
    def test_a_new_series_still_fills_a_chart(self):
        assert len(Series("x").counts(40)) == 40

    def test_padding_is_at_the_front(self):
        series = Series("x")
        series.add(1.0)
        assert series.counts(5) == [0, 0, 0, 0, 1]

    def test_asking_past_the_window_is_clamped(self):
        assert len(Series("x", window=10).counts(999)) == 10


class TestStatistics:
    def test_the_mean_is_the_mean(self):
        series = Series("x")
        for value in (10.0, 20.0, 30.0):
            series.add(value)
        assert series.mean() == 20.0

    def test_the_total_counts_observations(self):
        series = Series("x")
        for _ in range(7):
            series.add(1.0)
        assert series.total() == 7

    def test_the_sum_adds_values(self):
        series = Series("x")
        series.add(2.5)
        series.add(2.5)
        assert series.sum() == 5.0

    def test_errors_are_counted_separately(self):
        series = Series("x")
        series.add(1.0)
        series.add(1.0, error=True)
        assert (series.total(), series.errors()) == (2, 1)

    def test_the_maximum_is_kept(self):
        series = Series("x")
        for value in (5.0, 90.0, 12.0):
            series.add(value)
        assert series.buckets(1)[-1].maximum == 90.0

    def test_an_empty_series_reports_zero_rather_than_dividing(self):
        series = Series("x")
        assert (series.mean(), series.percentile(0.95), series.rate()) == (0.0, 0.0, 0.0)


class TestPercentiles:
    def test_the_median_of_a_known_set(self):
        series = Series("x")
        for value in range(1, 102):
            series.add(float(value))
        assert 45.0 <= series.percentile(0.5) <= 56.0

    def test_the_p95_sits_near_the_top(self):
        series = Series("x")
        for value in range(1, 102):
            series.add(float(value))
        assert series.percentile(0.95) >= 90.0

    def test_the_reservoir_keeps_the_tail(self):
        """Percentiles asked for here are upper ones, so a full reservoir must
        drop small observations rather than recent ones."""
        series = Series("x")
        for _ in range(RESERVOIR * 4):
            series.add(1.0)
        series.add(9999.0)
        assert series.percentile(1.0) == 9999.0

    def test_memory_stays_flat_under_load(self):
        series = Series("x")
        for value in range(10_000):
            series.add(float(value))
        assert len(series.buckets(1)[-1].samples) <= RESERVOIR


class TestPresentation:
    def test_a_sparkline_has_the_asked_for_number_of_points(self):
        assert len(Series("x").spark(10)) == 10

    def test_a_sparkline_of_nothing_is_flat(self):
        assert Series("x").spark(8) == [0.0] * 8

    def test_the_trend_compares_two_halves(self):
        series = Series("x")
        for index in range(3):
            series.add(1.0, at=minutes_ago(8 + index))
        for _ in range(10):
            series.add(1.0)
        assert series.trend(5) > 0

    def test_the_rate_counts_a_full_previous_minute_correctly(self):
        series = Series("x")
        for _ in range(60):
            series.add(1.0, at=minutes_ago(1))
        assert 30.0 <= series.rate(2) <= 60.0

    def test_the_first_observation_shows_a_rate_immediately(self):
        """A server that has just served its first request must not report
        zero for up to sixty seconds — that is the moment somebody is watching
        to see whether it worked."""
        series = Series("x")
        series.add(1.0)
        assert series.rate(5) > 0

    def test_a_single_observation_does_not_extrapolate_wildly(self):
        series = Series("x")
        series.add(1.0)
        assert series.rate(5) <= 12.0


class TestSeriesSet:
    def test_an_unknown_series_is_created_empty(self):
        assert SeriesSet()["never.seen"].total() == 0

    def test_the_same_name_gives_the_same_series(self):
        series = SeriesSet()
        assert series["a"] is series["a"]

    def test_membership_reflects_use(self):
        series = SeriesSet()
        assert "a" not in series
        series.add("a")
        assert "a" in series

    def test_names_are_listed_sorted(self):
        series = SeriesSet()
        series.add("z")
        series.add("a")
        assert series.names() == ["a", "z"]

    def test_clearing_one_leaves_the_others(self):
        series = SeriesSet()
        series.add("a")
        series.add("b")
        series.clear(["a"])
        assert (series["a"].total(), series["b"].total()) == (0, 1)


class TestLiveEdge:
    """A chart's right-hand edge is now, not the last time anything happened."""

    def test_a_series_that_went_quiet_shows_the_gap(self):
        series = Series("x")
        for _ in range(10):
            series.add(1.0, at=minutes_ago(5))
        assert series.counts(6)[-1] == 0

    def test_the_old_traffic_is_still_where_it_happened(self):
        series = Series("x")
        for _ in range(10):
            series.add(1.0, at=minutes_ago(5))
        assert series.counts(6)[0] == 10

    def test_a_stalled_series_reports_a_zero_rate(self):
        series = Series("x")
        for _ in range(600):
            series.add(1.0, at=minutes_ago(30))
        assert series.rate(5) == 0.0

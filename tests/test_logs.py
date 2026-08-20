"""The server's voice: what it writes, and what it refuses to write."""

from __future__ import annotations

import io
import logging
import time

from sillo.console.style import Palette, strip_ansi

from sillo_vise.config import LogConfig
from sillo_vise.logs import (
    AccessLog,
    Banner,
    JSONFormatter,
    RepeatFilter,
    ViseFormatter,
    attach_access_log,
    install_logging,
    silence_uvicorn,
)
from sillo_vise.recorder import Recorder, RequestEvent


def plain(stream: io.StringIO) -> str:
    return strip_ansi(stream.getvalue())


def access(**kwargs) -> tuple[AccessLog, io.StringIO]:
    stream = io.StringIO()
    return AccessLog(stream, palette=Palette(enabled=False), **kwargs), stream


class TestAccessLine:
    def test_carries_what_uvicorn_never_measured(self):
        log, stream = access()
        log.write(
            RequestEvent(
                method="GET",
                path="/x",
                status=200,
                duration_ms=38.0,
                response_bytes=12_400,
            )
        )
        assert "38ms" in stream.getvalue() and "12.4 kB" in stream.getvalue()

    def test_columns_line_up_across_methods(self):
        log, stream = access()
        for method in ("GET", "POST", "DELETE", "OPTIONS"):
            log.write(RequestEvent(method=method, path="/x", status=200))
        starts = {line.index("/x") for line in plain(stream).splitlines()}
        assert len(starts) == 1

    def test_a_slow_request_is_marked(self):
        log, stream = access()
        log.write(RequestEvent(path="/x", status=200, duration_ms=2104.0, slow=True))
        assert "slow" in plain(stream)

    def test_the_route_name_is_shown_when_it_was_not_slow(self):
        log, stream = access()
        log.write(RequestEvent(path="/x", status=200, route="api.documents.index"))
        assert "api.documents.index" in plain(stream)

    def test_a_long_path_is_truncated_rather_than_wrapped(self):
        log, stream = access()
        log.write(RequestEvent(path="/" + "a" * 200, status=200))
        assert len(plain(stream).strip()) < 120

    def test_the_query_can_be_left_off(self):
        log, stream = access(show_query=False)
        log.write(RequestEvent(path="/x", query="page=2", status=200))
        assert "page=2" not in plain(stream)


class TestWhatIsNotLogged:
    def test_the_dashboard_does_not_log_itself(self):
        log, stream = access(dashboard_path="/__sillo/foreman")
        log.write(RequestEvent(path="/__sillo/foreman/assets/index.js", status=200))
        assert stream.getvalue() == ""

    def test_it_can_be_asked_to(self):
        log, stream = access(dashboard_path="/__sillo/foreman", static=True)
        log.write(RequestEvent(path="/__sillo/foreman/assets/index.js", status=200))
        assert "index.js" in plain(stream)

    def test_application_requests_are_never_suppressed(self):
        log, stream = access(dashboard_path="/__sillo/foreman")
        log.write(RequestEvent(path="/api/v1/documents", status=200))
        assert "/api/v1/documents" in plain(stream)


class TestJSONStyle:
    def test_one_object_per_line(self):
        import json

        log, stream = access(json=True)
        log.write(RequestEvent(method="GET", path="/x", status=200, duration_ms=38.0))
        assert json.loads(stream.getvalue())["duration_ms"] == 38.0

    def test_headers_never_reach_the_log(self):
        log, stream = access(json=True)
        log.write(
            RequestEvent(path="/x", status=200, headers=[("authorization", "Bearer x")])
        )
        assert "authorization" not in stream.getvalue()


class TestFormatter:
    def test_the_message_starts_at_the_same_column_every_line(self):
        formatter = ViseFormatter(Palette(enabled=False))
        lines = [
            formatter.format(
                logging.LogRecord(
                    "sillo.record", level, "", 0, "the message", None, None
                )
            )
            for level in (
                logging.DEBUG,
                logging.INFO,
                logging.WARNING,
                logging.CRITICAL,
            )
        ]
        assert len({line.index("the message") for line in lines}) == 1

    def test_a_long_logger_name_is_truncated(self):
        formatter = ViseFormatter(Palette(enabled=False))
        line = formatter.format(
            logging.LogRecord(
                "a.very.long.logger.name.indeed", 20, "", 0, "m", None, None
            )
        )
        assert "…" in line

    def test_a_traceback_is_indented_under_its_line(self):
        formatter = ViseFormatter(Palette(enabled=False))
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord("x", 40, "", 0, "failed", None, sys.exc_info())
        assert "\n      " in formatter.format(record)

    def test_plain_style_carries_no_escape_sequences(self):
        formatter = ViseFormatter(Palette(enabled=False))
        line = formatter.format(logging.LogRecord("x", 40, "", 0, "m", None, None))
        assert "\x1b" not in line


class TestJSONFormatter:
    def test_the_schema_is_narrow(self):
        import json

        payload = json.loads(
            JSONFormatter().format(logging.LogRecord("x", 20, "", 0, "m", None, None))
        )
        assert set(payload) == {"at", "level", "logger", "message"}

    def test_structured_extras_are_nested(self):
        import json

        record = logging.LogRecord("x", 20, "", 0, "m", None, None)
        record.request_id = "r1"
        payload = json.loads(JSONFormatter().format(record))
        assert payload["fields"]["request_id"] == "r1"

    def test_an_extra_cannot_shadow_a_field(self):
        import json

        record = logging.LogRecord("x", 20, "", 0, "m", None, None)
        record.level = "nonsense"
        payload = json.loads(JSONFormatter().format(record))
        assert payload["level"] == "info"


class TestUvicorn:
    def test_its_loggers_are_silenced(self):
        logging.getLogger("uvicorn.access").addHandler(logging.NullHandler())
        silence_uvicorn()
        assert logging.getLogger("uvicorn.access").handlers == []

    def test_they_no_longer_propagate(self):
        silence_uvicorn()
        assert logging.getLogger("uvicorn").propagate is False

    def test_a_bind_failure_can_still_be_reported(self):
        """uvicorn.error is where "address already in use" arrives. Silencing
        it outright turns a clear message into an unexplained exit."""
        silence_uvicorn()
        assert logging.getLogger("uvicorn.error").level <= logging.WARNING


class TestInstallation:
    def test_a_project_that_configured_logging_is_left_alone(self):
        root = logging.getLogger()
        existing = logging.NullHandler()
        root.addHandler(existing)
        try:
            install_logging(LogConfig())
            assert existing in root.handlers
        finally:
            root.removeHandler(existing)

    def test_force_replaces_it(self):
        root = logging.getLogger()
        existing = logging.NullHandler()
        root.addHandler(existing)
        try:
            install_logging(LogConfig(), stream=io.StringIO(), force=True)
            assert existing not in root.handlers
        finally:
            root.handlers.clear()

    def test_the_level_is_applied(self):
        install_logging(LogConfig(level="warning"), stream=io.StringIO(), force=True)
        assert logging.getLogger().level == logging.WARNING
        logging.getLogger().handlers.clear()


class TestAccessLogFollowsTheRecorder:
    def test_a_recorded_request_produces_a_line(self):
        recorder = Recorder()
        stream = io.StringIO()
        attach_access_log(recorder, LogConfig(), stream=stream)
        recorder.request(method="GET", path="/x", status=200)
        assert "/x" in strip_ansi(stream.getvalue())

    def test_other_kinds_produce_nothing(self):
        recorder = Recorder()
        stream = io.StringIO()
        attach_access_log(recorder, LogConfig(), stream=stream)
        recorder.query("SELECT 1")
        assert stream.getvalue() == ""

    def test_turning_access_off_attaches_nothing(self):
        recorder = Recorder()
        assert attach_access_log(recorder, LogConfig(access=False)) is None

    def test_a_paused_recorder_writes_no_access_lines(self):
        """The access log reads the recorder, so the two can never disagree
        about what happened."""
        recorder = Recorder()
        stream = io.StringIO()
        attach_access_log(recorder, LogConfig(), stream=stream)
        recorder.pause()
        recorder.request(path="/x", status=200)
        assert stream.getvalue() == ""


class TestBanner:
    def test_it_shows_the_urls(self):
        stream = io.StringIO()
        Banner(stream, Palette(enabled=False)).write(
            version="0.1.0",
            framework="0.2.1",
            python="3.12",
            links=[("Local", "http://127.0.0.1:8000")],
        )
        assert "http://127.0.0.1:8000" in stream.getvalue()

    def test_labels_line_up(self):
        stream = io.StringIO()
        Banner(stream, Palette(enabled=False)).write(
            version="0.1.0",
            framework="0.2.1",
            python="3.12",
            links=[("Local", "A"), ("Foreman", "B"), ("App", "C")],
        )
        rows = [line for line in stream.getvalue().splitlines() if "➜" in line]
        assert (
            len({row.index(value) for row, value in zip(rows, "ABC", strict=True)}) == 1
        )

    def test_notes_are_shown(self):
        stream = io.StringIO()
        Banner(stream, Palette(enabled=False)).write(
            version="0.1.0",
            framework="0.2.1",
            python="3.12",
            links=[],
            notes=["reload on", "recorder on"],
        )
        assert "reload on · recorder on" in stream.getvalue()

    def test_stopping_says_so(self):
        stream = io.StringIO()
        Banner(stream, Palette(enabled=False)).stopped("interrupted")
        assert "vise stopped — interrupted" in stream.getvalue()


class TestRepeats:
    """sillo reports an unhandled exception from two layers. One failure should
    reach the screen once."""

    INNER = (
        "Traceback (most recent call last):\n"
        '  File "/a/router.py", line 10, in handle\n'
        "    handler()\n"
        '  File "/app/main.py", line 33, in boom\n'
        '    raise ValueError("no")\n'
        "ValueError: no"
    )

    OUTER = (
        "Traceback (most recent call last):\n"
        '  File "/a/error.py", line 9, in __call__\n'
        "    await app()\n"
        '  File "/a/router.py", line 10, in handle\n'
        "    handler()\n"
        '  File "/app/main.py", line 33, in boom\n'
        '    raise ValueError("no")\n'
        "ValueError: no"
    )

    @staticmethod
    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord("x", logging.ERROR, "", 0, message, None, None)

    def test_the_first_report_is_printed(self):
        assert RepeatFilter().filter(self.record(self.INNER))

    def test_the_same_failure_from_another_layer_is_not(self):
        repeat = RepeatFilter()
        repeat.filter(self.record(self.INNER))
        assert not repeat.filter(self.record(self.OUTER))

    def test_a_different_failure_is_printed(self):
        repeat = RepeatFilter()
        repeat.filter(self.record(self.INNER))
        assert repeat.filter(self.record(self.INNER.replace("line 33", "line 44")))

    def test_a_different_exception_at_the_same_line_is_printed(self):
        repeat = RepeatFilter()
        repeat.filter(self.record(self.INNER))
        assert repeat.filter(
            self.record(self.INNER.replace("ValueError: no", "KeyError: k"))
        )

    def test_an_identical_ordinary_line_is_suppressed(self):
        repeat = RepeatFilter()
        repeat.filter(self.record("connection lost"))
        assert not repeat.filter(self.record("connection lost"))

    def test_a_repeat_after_the_window_is_printed(self):
        """A loop logging "retrying" forty times is forty things happening."""
        repeat = RepeatFilter(window=0.01)
        repeat.filter(self.record("retrying"))
        time.sleep(0.02)
        assert repeat.filter(self.record("retrying"))

    def test_two_different_lines_both_print(self):
        repeat = RepeatFilter()
        repeat.filter(self.record("first"))
        assert repeat.filter(self.record("second"))

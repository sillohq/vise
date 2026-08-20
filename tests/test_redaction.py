"""
Redaction, proved where it matters.

Every assertion here reads the store, never the dashboard API. A test that
checks the interface hides a credential passes just as happily when the
credential is sitting in memory behind a filter, and a filter is one bug away
from being no filter. What has to be true is that the secret was never stored.
"""

from __future__ import annotations

import pytest

from sillo_vise.config import RecorderConfig
from sillo_vise.recorder import PLACEHOLDER, EventKind, Recorder, Redactor

SECRET = "live_sk_do_not_store_this"


@pytest.fixture
def recorder() -> Recorder:
    return Recorder(RecorderConfig(redact=("x-tenant-key",)))


def stored_text(recorder: Recorder) -> str:
    """Everything the store holds, flattened, so a leak anywhere is visible."""
    return repr([event.to_dict() for event in recorder.store])


class TestHeaders:
    def test_authorization_never_reaches_the_store(self, recorder):
        recorder.request(headers=[("authorization", f"Bearer {SECRET}")])
        assert SECRET not in stored_text(recorder)

    def test_the_header_still_appears_by_name(self, recorder):
        event = recorder.request(headers=[("authorization", f"Bearer {SECRET}")])
        assert event.headers == [("authorization", PLACEHOLDER)]

    def test_cookies_go_both_ways(self, recorder):
        recorder.request(
            headers=[("cookie", f"session={SECRET}")],
            response_headers=[("set-cookie", f"session={SECRET}")],
        )
        assert SECRET not in stored_text(recorder)

    def test_a_project_name_is_honoured(self, recorder):
        recorder.request(headers=[("X-Tenant-Key", SECRET)])
        assert SECRET not in stored_text(recorder)

    def test_order_and_duplicates_survive(self, recorder):
        event = recorder.request(
            headers=[("accept", "a"), ("accept", "b"), ("authorization", SECRET)]
        )
        assert [name for name, _ in event.headers] == [
            "accept",
            "accept",
            "authorization",
        ]

    def test_ordinary_headers_are_untouched(self, recorder):
        event = recorder.request(headers=[("accept", "application/json")])
        assert event.headers == [("accept", "application/json")]


class TestQueryStrings:
    def test_named_parameters_are_redacted(self, recorder):
        recorder.request(query=f"page=2&api_key={SECRET}")
        assert SECRET not in stored_text(recorder)

    def test_the_rest_of_the_query_survives(self, recorder):
        event = recorder.request(query=f"page=2&token={SECRET}&sort=name")
        assert event.query == f"page=2&token={PLACEHOLDER}&sort=name"

    def test_a_valueless_key_is_left_alone(self, recorder):
        assert recorder.request(query="verbose").query == "verbose"

    def test_full_path_reflects_the_redacted_query(self, recorder):
        event = recorder.request(path="/x", query=f"token={SECRET}")
        assert SECRET not in event.full_path


class TestOutgoing:
    def test_userinfo_is_removed_from_a_url(self, recorder):
        recorder.outgoing(f"https://user:{SECRET}@api.test/v1")
        assert SECRET not in stored_text(recorder)

    def test_query_credentials_are_removed_from_a_url(self, recorder):
        recorder.outgoing(f"https://api.test/v1?api_key={SECRET}")
        assert SECRET not in stored_text(recorder)

    def test_the_host_and_path_survive(self, recorder):
        event = recorder.outgoing(f"https://api.test/v1/charge?secret={SECRET}")
        assert event.url.startswith("https://api.test/v1/charge")


class TestFreeText:
    def test_a_credential_in_a_log_line_is_caught(self, recorder):
        recorder.log("info", f"calling upstream with Bearer {SECRET}")
        assert SECRET not in stored_text(recorder)

    def test_an_exception_message_is_caught(self, recorder):
        recorder.exception(ValueError(f"bad token={SECRET}"))
        assert SECRET not in stored_text(recorder)

    def test_an_ordinary_message_is_untouched(self, recorder):
        event = recorder.log("info", "job notifications.digest enqueued")
        assert event.message == "job notifications.digest enqueued"


class TestJobPayloads:
    def test_a_named_argument_is_redacted(self, recorder):
        recorder.job("mail.send", payload={"to": "a@b.test", "password": SECRET})
        assert SECRET not in stored_text(recorder)

    def test_other_arguments_survive(self, recorder):
        event = recorder.job("mail.send", payload={"to": "a@b.test", "secret": SECRET})
        assert event.payload["to"] == "a@b.test"


class TestSQLBindings:
    def test_bindings_are_kept_by_default(self):
        recorder = Recorder(RecorderConfig())
        event = recorder.query("SELECT 1 WHERE x = $1", params=["visible"])
        assert event.params == ["visible"]

    def test_bindings_become_types_when_asked(self):
        recorder = Recorder(RecorderConfig(redact_bindings=True))
        event = recorder.query("SELECT 1 WHERE x = $1", params=[SECRET])
        assert event.params == ["str"]

    def test_mapping_bindings_keep_their_keys(self):
        recorder = Recorder(RecorderConfig(redact_bindings=True))
        event = recorder.query("SELECT 1", params={"token": SECRET})
        assert event.params == {"token": "str"}


class TestBodies:
    def test_bodies_are_truncated_to_the_ceiling(self):
        redactor = Redactor()
        assert len(redactor.body("x" * 5000, 100)) < 200

    def test_truncation_says_how_much_was_dropped(self):
        assert "5000 bytes" in Redactor().body("x" * 5000, 100)

    def test_a_short_body_is_returned_whole(self):
        assert Redactor().body("hello", 100) == "hello"


class TestRedactorItself:
    def test_repr_does_not_list_protected_names(self):
        text = repr(Redactor(["authorization"], ["api_key"]))
        assert "authorization" not in text and "api_key" not in text

    def test_names_are_matched_case_insensitively(self):
        redactor = Redactor(["Authorization"])
        assert redactor.headers_of({"AUTHORIZATION": SECRET}) == {
            "AUTHORIZATION": PLACEHOLDER
        }

    def test_nothing_configured_redacts_nothing(self):
        assert (
            Redactor().headers_of({"authorization": SECRET})["authorization"] == SECRET
        )


class TestPausing:
    def test_a_paused_recorder_stores_nothing(self, recorder):
        recorder.pause()
        recorder.request(path="/x")
        assert recorder.store.retained(EventKind.REQUEST) == 0

    def test_resuming_works(self, recorder):
        recorder.pause()
        recorder.resume()
        recorder.request(path="/x")
        assert recorder.store.retained(EventKind.REQUEST) == 1


class TestTheNetUnderTheNet:
    """Free text is free, so this is a net, not the mechanism. It should still
    not mangle ordinary prose on its way past."""

    def test_an_auth_scheme_is_caught(self):
        assert (
            Redactor().text("calling with Bearer abc123")
            == f"calling with Bearer {PLACEHOLDER}"
        )

    def test_a_named_credential_is_caught(self):
        assert (
            Redactor().text("user password: hunter2") == f"user password={PLACEHOLDER}"
        )

    def test_prose_mentioning_a_keyword_is_left_alone(self):
        assert Redactor().text("password reset requested") == "password reset requested"

    def test_a_url_credential_is_caught(self):
        assert "hunter2" not in Redactor().text("dialling postgres://me:hunter2@db/app")


class TestTracebacks:
    """A traceback ends with the exception's own message and carries the source
    line of every frame, so anything the message holds it holds too."""

    def test_a_credential_in_a_traceback_is_redacted(self, recorder):
        try:
            raise ValueError(f"bad token={SECRET}")
        except ValueError as error:
            recorder.exception(error)

        assert SECRET not in stored_text(recorder)

    def test_the_traceback_is_still_readable(self, recorder):
        try:
            raise ValueError("ordinary failure")
        except ValueError as error:
            recorder.exception(error)

        assert (
            "ValueError: ordinary failure"
            in recorder.store.recent(EventKind.EXCEPTION)[0].traceback
        )

    def test_a_job_traceback_is_redacted(self, recorder):
        recorder.job(
            "mail.send",
            status="failed",
            error=f"failed with token={SECRET}",
            traceback=f'  File "x.py", line 1\\n    send(token="{SECRET}")\\nValueError: token={SECRET}',
        )
        assert SECRET not in stored_text(recorder)

"""Configuration: discovery, parsing, precedence and refusal."""

from __future__ import annotations

import pytest

from sillo_vise.config import (
    ConfigError,
    ViseConfig,
    find_config_file,
    load_config,
    parse_config,
    render_template,
)


class TestDefaults:
    def test_empty_file_is_all_defaults(self):
        config = parse_config("")
        assert config == ViseConfig(source=None)

    def test_dashboard_is_loopback_only_by_default(self):
        assert parse_config("").dashboard.access == "local"

    def test_bodies_are_not_captured_by_default(self):
        assert parse_config("").recorder.capture_bodies is False

    def test_source_is_recorded(self):
        assert parse_config("", source="/tmp/.vise").source == "/tmp/.vise"


class TestParsing:
    def test_reads_a_section(self):
        config = parse_config("[server]\nport = 9001\nhost = '0.0.0.0'")
        assert (config.server.host, config.server.port) == ("0.0.0.0", 9001)

    def test_untouched_keys_keep_their_defaults(self):
        config = parse_config("[server]\nport = 9001")
        assert config.server.reload is True

    def test_arrays_become_tuples(self):
        config = parse_config("[server]\nwatch = ['a', 'b']")
        assert config.server.watch == ("a", "b")

    def test_unknown_section_is_refused(self):
        with pytest.raises(ConfigError, match="Unknown section"):
            parse_config("[nonsense]\nx = 1")

    def test_unknown_key_is_refused_by_name(self):
        with pytest.raises(ConfigError, match="prot"):
            parse_config("[server]\nprot = 8000")

    def test_wrong_type_is_refused(self):
        with pytest.raises(ConfigError, match="whole number"):
            parse_config("[server]\nport = 'eight thousand'")

    def test_invalid_toml_names_the_file(self):
        with pytest.raises(ConfigError, match="not valid TOML"):
            parse_config("[server\nport = 1")


class TestRedaction:
    def test_defaults_cover_the_usual_credentials(self):
        headers = parse_config("").recorder.redacted_headers
        assert {"authorization", "cookie", "set-cookie"} <= headers

    def test_project_names_extend_rather_than_replace(self):
        config = parse_config("[recorder]\nredact = ['x-tenant-key']")
        headers = config.recorder.redacted_headers
        assert "x-tenant-key" in headers and "authorization" in headers

    def test_names_are_matched_case_insensitively(self):
        config = parse_config("[recorder]\nredact = ['X-Tenant-Key']")
        assert "x-tenant-key" in config.recorder.redacted_headers


class TestDiscovery:
    def test_finds_the_file_in_the_directory(self, tmp_path):
        (tmp_path / ".vise").write_text("[server]\nport = 9100")
        assert find_config_file(tmp_path) == tmp_path / ".vise"

    def test_walks_upward(self, tmp_path):
        (tmp_path / ".vise").write_text("")
        nested = tmp_path / "app" / "http"
        nested.mkdir(parents=True)
        assert find_config_file(nested) == tmp_path / ".vise"

    def test_stops_at_a_repository_boundary(self, tmp_path):
        (tmp_path / ".vise").write_text("")
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        assert find_config_file(project) is None

    def test_no_file_anywhere_gives_defaults(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert load_config(tmp_path, environ={}) == ViseConfig()


class TestPrecedence:
    def test_environment_beats_the_file(self, tmp_path):
        (tmp_path / ".vise").write_text("[server]\nport = 9001")
        config = load_config(tmp_path, environ={"VISE_SERVER_PORT": "9500"})
        assert config.server.port == 9500

    def test_flags_beat_the_environment(self, tmp_path):
        (tmp_path / ".vise").write_text("[server]\nport = 9001")
        config = load_config(
            tmp_path,
            environ={"VISE_SERVER_PORT": "9500"},
            overrides={"server": {"port": 9999}},
        )
        assert config.server.port == 9999

    def test_an_override_leaves_its_neighbours_alone(self, tmp_path):
        (tmp_path / ".vise").write_text("[recorder]\nbuffer = 50")
        config = load_config(tmp_path, environ={}, overrides={"server": {"port": 1}})
        assert config.recorder.buffer == 50

    def test_environment_booleans(self, tmp_path):
        (tmp_path / ".git").mkdir()
        config = load_config(tmp_path, environ={"VISE_SERVER_RELOAD": "off"})
        assert config.server.reload is False

    def test_environment_lists_are_comma_separated(self, tmp_path):
        (tmp_path / ".git").mkdir()
        config = load_config(tmp_path, environ={"VISE_RECORDER_REDACT": "a, b"})
        assert config.recorder.redact == ("a", "b")

    def test_unrecognised_variables_are_left_alone(self, tmp_path):
        (tmp_path / ".git").mkdir()
        config = load_config(tmp_path, environ={"VISE_SERVER_NONSENSE": "1"})
        assert config == ViseConfig()


class TestRendering:
    def test_the_template_parses(self):
        parse_config(render_template())

    def test_a_found_application_is_written_live(self):
        assert 'target = "app.main:app"' in render_template("app.main:app")
        assert parse_config(render_template("app.main:app")).app.target == "app.main:app"

    def test_everything_else_stays_commented(self):
        assert parse_config(render_template()) == ViseConfig()


class TestReporting:
    def test_to_dict_hides_the_token(self):
        config = parse_config("[dashboard]\ntoken = 'hunter2'")
        assert config.to_dict()["dashboard"]["token"] == "***"

    def test_to_dict_keeps_the_shape_of_the_file(self):
        data = parse_config("").to_dict()
        assert set(data) >= {"app", "server", "dashboard", "recorder", "logs", "panels"}

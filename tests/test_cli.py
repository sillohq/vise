"""
The ``vise`` command.

Every command is run through the real console, against real files in a temporary
directory. What is being checked is mostly refusal: a command that overwrites a
file somebody edited, or that silently discards a project's configuration, is
worse than one that does nothing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from sillo.console import Console
from sillo.console.style import strip_ansi

from sillo_vise.cli import COMMANDS, build_console
from sillo_vise.cli.discover import discover_target
from sillo_vise.config import CONFIG_FILENAME, ViseConfig, parse_config


@pytest.fixture
def console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    shell = Console(prog="vise", output=out, error=out, color=False, interactive=False)
    shell.add_many(COMMANDS)
    return shell, out


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """A directory that looks like a sillo project, as the working directory."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "main.py").write_text(
        "from sillo import SilloApp\n"
        "app = SilloApp(title='Discovered')\n"
        "async def home(request, response):\n"
        "    return response.json({})\n"
        "app.get('/', handler=home, name='web.home')\n"
    )
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestConsole:
    def test_every_command_is_registered(self):
        assert len(build_console()._commands) >= len(COMMANDS)

    def test_help_lists_serve_first(self, console):
        shell, out = console
        shell.run([])
        assert "serve" in strip_ansi(out.getvalue())

    def test_every_command_has_a_help_line(self):
        assert all(command.help for command in COMMANDS)

    def test_every_command_has_a_name(self):
        assert all(command.name for command in COMMANDS)

    def test_names_are_unique(self):
        names = [command.name for command in COMMANDS]
        assert len(names) == len(set(names))


class TestInit:
    def test_it_writes_the_file(self, console, project):
        shell, _ = console
        assert shell.run(["init"]) == 0
        assert (project / CONFIG_FILENAME).is_file()

    def test_what_it_writes_parses(self, console, project):
        shell, _ = console
        shell.run(["init"])
        parse_config((project / CONFIG_FILENAME).read_text())

    def test_it_writes_the_discovered_application(self, console, project):
        shell, _ = console
        shell.run(["init"])
        config = parse_config((project / CONFIG_FILENAME).read_text())
        assert config.app.target == "app.main:app"

    def test_everything_else_stays_at_its_default(self, console, project):
        shell, _ = console
        shell.run(["init"])
        config = parse_config((project / CONFIG_FILENAME).read_text())
        assert config.server == ViseConfig().server

    def test_it_refuses_to_overwrite(self, console, project):
        """A .vise is a file somebody edited."""
        shell, out = console
        (project / CONFIG_FILENAME).write_text("[server]\nport = 9000\n")
        assert shell.run(["init"]) != 0
        assert "already exists" in strip_ansi(out.getvalue())

    def test_the_existing_file_is_untouched(self, console, project):
        shell, _ = console
        (project / CONFIG_FILENAME).write_text("[server]\nport = 9000\n")
        shell.run(["init"])
        assert "9000" in (project / CONFIG_FILENAME).read_text()

    def test_force_overwrites(self, console, project):
        shell, _ = console
        (project / CONFIG_FILENAME).write_text("[server]\nport = 9000\n")
        assert shell.run(["init", "--force"]) == 0
        assert "9000" not in (project / CONFIG_FILENAME).read_text()


class TestDiscovery:
    def test_it_finds_the_conventional_layout(self, project):
        assert discover_target(ViseConfig(), project) == "app.main:app"

    def test_the_config_wins(self, project):
        from sillo_vise.config import AppConfig

        config = ViseConfig(app=AppConfig(target="other:app"))
        assert discover_target(config, project) == "other:app"

    def test_pyproject_is_read(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.sillo]\napp = "named:app"\n')
        assert discover_target(ViseConfig(), tmp_path) == "named:app"

    def test_a_broken_pyproject_does_not_stop_discovery(self, project):
        """Parsing by hand, so a syntax error in an unrelated section does not
        stop vise finding an application it could have found by reading two
        lines."""
        (project / "pyproject.toml").write_text("[[[ this is not toml\n")
        assert discover_target(ViseConfig(), project) == "app.main:app"

    def test_the_environment_is_honoured(self, project, monkeypatch):
        monkeypatch.setenv("SILLO_APP", "from_env:app")
        assert discover_target(ViseConfig(), project) == "from_env:app"

    def test_nothing_is_found_in_an_empty_directory(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SILLO_APP", raising=False)
        assert discover_target(ViseConfig(), tmp_path) is None

    def test_discovery_does_not_import_the_application(self, tmp_path, monkeypatch):
        """Importing to find out whether something is importable runs the
        project's module as a side effect of a question."""
        monkeypatch.delenv("SILLO_APP", raising=False)
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "__init__.py").write_text("")
        (tmp_path / "app" / "main.py").write_text("raise SystemExit('imported!')\n")

        assert discover_target(ViseConfig(), tmp_path) == "app.main:app"


class TestDoctor:
    def test_it_reports_the_panels(self, console, project):
        shell, out = console
        shell.run(["doctor"])
        text = strip_ansi(out.getvalue())
        assert "Panels" in text and "overview" in text

    def test_it_says_why_a_panel_is_missing(self, console, project):
        shell, out = console
        shell.run(["doctor"])
        assert "database" in strip_ansi(out.getvalue())

    def test_it_exits_non_zero_when_panels_are_missing(self, console, project):
        """So it is usable in a check without parsing the output."""
        shell, _ = console
        assert shell.run(["doctor"]) == 1

    def test_it_reports_the_versions(self, console, project):
        shell, out = console
        shell.run(["doctor"])
        assert "sillo" in strip_ansi(out.getvalue())

    def test_it_fails_clearly_with_no_application(self, console, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SILLO_APP", raising=False)
        shell, out = console
        assert shell.run(["doctor"]) != 0
        assert "No application found" in strip_ansi(out.getvalue())


class TestPanels:
    def test_it_lists_them(self, console, project):
        shell, out = console
        assert shell.run(["panels"]) == 0
        assert "overview" in strip_ansi(out.getvalue())

    def test_it_can_be_filtered_by_group(self, console, project):
        shell, out = console
        shell.run(["panels", "--group", "Tools"])
        text = strip_ansi(out.getvalue())
        assert "routes" in text and "overview" not in text

    def test_an_unknown_group_is_reported(self, console, project):
        shell, out = console
        assert shell.run(["panels", "--group", "Nonsense"]) != 0


class TestRoutes:
    def test_it_lists_the_routes(self, console, project):
        shell, out = console
        assert shell.run(["routes"]) == 0
        assert "web.home" in strip_ansi(out.getvalue())

    def test_it_filters_by_path(self, console, project):
        shell, out = console
        shell.run(["routes", "--path", "openapi"])
        text = strip_ansi(out.getvalue())
        assert "openapi" in text and "web.home" not in text

    def test_no_match_reports_rather_than_printing_nothing(self, console, project):
        shell, out = console
        assert shell.run(["routes", "--path", "nowhere"]) != 0
        assert "No routes matched" in strip_ansi(out.getvalue())


class TestVersion:
    def test_it_reports_the_versions(self, console):
        shell, out = console
        assert shell.run(["version"]) == 0
        text = strip_ansi(out.getvalue())
        assert "vise" in text and "python" in text

    def test_it_says_what_an_optional_package_would_enable(self, console):
        shell, out = console
        shell.run(["version"])
        assert "panel" in strip_ansi(out.getvalue())


class TestServeOverrides:
    """A flag left untyped must never override the .vise file."""

    def build(self, argv: list[str]):
        from sillo.console.arguments import parse

        from sillo_vise.cli.serve import Serve

        parsed = parse(Serve.arguments, argv, command="serve")
        command = Serve(parsed, None, None)  # type: ignore[arg-type]
        return command._overrides()

    def test_nothing_typed_overrides_nothing(self):
        assert self.build([]) == {}

    def test_a_typed_port_overrides(self):
        assert self.build(["--port", "9000"]) == {"server": {"port": 9000}}

    def test_reload_off_overrides(self):
        assert self.build(["--reload", "off"]) == {"server": {"reload": False}}

    def test_reload_on_overrides(self):
        assert self.build(["--reload", "on"]) == {"server": {"reload": True}}

    def test_quiet_silences_both(self):
        assert self.build(["--quiet"]) == {"logs": {"banner": False, "access": False}}

    def test_the_recorder_can_be_switched_off(self):
        assert self.build(["--record", "off"]) == {"recorder": {"enabled": False}}

    def test_an_override_does_not_touch_its_neighbours(self):
        assert self.build(["--port", "9000"]).get("recorder") is None

"""Exercise consent and the actual CLI, asserting the entire outbound payload."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import click
import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from caliper import config, telemetry
from caliper.main import app

runner = CliRunner()


@pytest.fixture
def deliveries(monkeypatch):
    sent = []
    monkeypatch.setattr(telemetry, "POSTHOG_PROJECT_TOKEN", "phc_test")
    monkeypatch.setattr(telemetry, "version", lambda package: "0.21.0")
    monkeypatch.setattr(
        telemetry, "_post", lambda host, body: sent.append((host, json.loads(body)))
    )
    return sent


def test_consent_controls_default_off_enable_and_disable(deliveries):
    result = runner.invoke(app, ["telemetry", "status"])
    assert result.exit_code == 0
    assert "Saved preference: disabled" in result.output
    assert not (config.config_home() / "telemetry.json").exists()
    runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert deliveries == []

    result = runner.invoke(app, ["telemetry", "enable"])
    assert result.exit_code == 0
    assert "Collection enabled. Destination: https://us.i.posthog.com" in result.output
    assert json.loads((config.config_home() / "telemetry.json").read_text()) == {
        "enabled": True
    }
    assert deliveries == []
    runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert len(deliveries) == 1

    result = runner.invoke(app, ["telemetry", "disable"])
    assert result.exit_code == 0
    runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert len(deliveries) == 1
    assert json.loads((config.config_home() / "telemetry.json").read_text()) == {
        "enabled": False
    }


def test_environment_kill_switch_overrides_saved_consent(monkeypatch, deliveries):
    telemetry.set_enabled(True)
    monkeypatch.setenv("CALIPER_TELEMETRY_DISABLED", "1")
    runner.invoke(app, ["validate", "missing.eval.yaml"])
    status = runner.invoke(app, ["telemetry", "status"])
    assert "Saved preference: enabled" in status.output
    assert "Collection disabled by CALIPER_TELEMETRY_DISABLED" in status.output
    assert deliveries == []


@pytest.mark.parametrize(
    "contents",
    [
        "invalid",
        "[]",
        "null",
        '"enabled"',
        '{"enabled": "true"}',
        '{"enabled": 1}',
        '{"enabled": false}',
    ],
)
def test_invalid_or_disabled_consent_sends_nothing(contents, deliveries):
    home = config.config_home()
    (home / "telemetry.json").write_text(contents)
    result = runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert result.exit_code == 1
    assert deliveries == []


def test_unreadable_config_does_not_change_command_result(monkeypatch, deliveries):
    def unavailable():
        raise PermissionError("private config location")

    monkeypatch.setattr(config, "config_home", unavailable)
    result = runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert result.exit_code == 1
    assert "File not found" in result.output
    assert "private config location" not in result.output
    assert deliveries == []
    result = runner.invoke(app, ["telemetry", "enable"])
    assert result.exit_code == 2
    assert "Cannot save telemetry preference" in result.output


def test_whole_payload_excludes_paths_models_errors_and_arguments(
    monkeypatch, tmp_path, deliveries
):
    telemetry.set_enabled(True)
    ticks = iter([100.0, 112.0])
    monkeypatch.setattr(telemetry.time, "monotonic", lambda: next(ticks))
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "private-customer.eval.yaml"),
            "--model",
            "codex:private-model",
            "--output",
            str(tmp_path / "private-output.json"),
            "--ablate",
            "private-skill",
        ],
    )
    assert result.exit_code == 1
    assert "private-customer" in result.output
    assert deliveries == [
        (
            "us.i.posthog.com",
            {
                "api_key": "phc_test",
                "event": "caliper_command_completed",
                "distinct_id": "caliper-anonymous",
                "properties": {
                    "schema_version": 1,
                    "command": "run",
                    "caliper_version": "0.21.0",
                    "os_family": {
                        "darwin": "macos",
                        "win32": "windows",
                        "linux": "linux",
                    }.get(sys.platform, "other"),
                    "backend": "codex",
                    "outcome": "bad_input",
                    "duration_bucket": "10s_60s",
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    "$ip": "0.0.0.0",
                },
            },
        )
    ]


def test_successful_validate_sends_one_event(tmp_path, deliveries):
    spec = tmp_path / "secret.eval.yaml"
    spec.write_text(
        "tasks:\n  - name: Secret\n    prompt: Private customer data\n    assert: assert True\n"
    )
    telemetry.set_enabled(True)
    result = runner.invoke(app, ["validate", str(spec)])
    assert result.exit_code == 0, result.output
    assert len(deliveries) == 1
    properties = deliveries[0][1]["properties"]
    assert properties["command"] == "validate"
    assert properties["outcome"] == "success"
    assert properties["backend"] is None
    assert "Private customer data" not in json.dumps(deliveries)


@pytest.mark.parametrize(
    ("target", "backend"),
    [
        ("codex", "codex"),
        ("pi:secret", "pi"),
        ("hermes:secret", "hermes"),
        ("claude:secret", "claude-code"),
        ("secret-model", "claude-code"),
        ("customer-backend:secret", None),
    ],
)
def test_only_registered_backend_names_leave_machine(target, backend, deliveries):
    telemetry.set_enabled(True)
    runner.invoke(app, ["run", "missing.eval.yaml", "--model", target])
    assert deliveries[0][1]["properties"]["backend"] == backend


@pytest.mark.parametrize(
    ("exception", "exit_code", "outcome"),
    [
        (typer.Exit(0), 0, "success"),
        (typer.Exit(1), 1, "bad_input"),
        (typer.Exit(2), 2, "cannot_run"),
        (typer.Exit(3), 3, "bar_not_met"),
        (typer.Exit(130), 130, "interrupted"),
        (KeyboardInterrupt(), None, "interrupted"),
        (typer.Abort(), 1, "interrupted"),
        (click.Abort(), 1, "interrupted"),
        (RuntimeError("private traceback"), 1, "error"),
        (SystemExit(2), 2, "cannot_run"),
    ],
)
def test_exit_and_exception_behavior_is_preserved(
    exception, exit_code, outcome, deliveries
):
    sample = typer.Typer()

    @sample.command("list", cls=telemetry.TelemetryCommand)
    def command():
        raise exception

    telemetry.set_enabled(True)
    result = runner.invoke(sample, [])
    if exit_code is None:
        baseline = typer.Typer()
        baseline.command("list")(command)
        exit_code = runner.invoke(baseline, []).exit_code
    assert result.exit_code == exit_code
    assert len(deliveries) == 1
    assert deliveries[0][1]["properties"]["outcome"] == outcome
    assert "private traceback" not in json.dumps(deliveries)


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["run", "--help"],
        ["run"],
        ["validate", "--unknown"],
        ["unknown-secret-command"],
        ["telemetry", "status"],
        ["telemetry", "enable"],
        ["telemetry", "disable"],
    ],
)
def test_help_parse_errors_and_consent_commands_send_nothing(args, deliveries):
    telemetry.set_enabled(True)
    runner.invoke(app, args)
    assert deliveries == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CALIPER_TELEMETRY_PROJECT_TOKEN", ""),
        ("CALIPER_TELEMETRY_PROJECT_TOKEN", "phx_private_key"),
        ("CALIPER_TELEMETRY_HOST", "http://us.i.posthog.com"),
        ("CALIPER_TELEMETRY_HOST", "https://attacker.example"),
        ("CALIPER_TELEMETRY_HOST", "https://us.i.posthog.com@attacker.example"),
    ],
)
def test_unconfigured_or_invalid_destination_fails_closed(
    monkeypatch, name, value, deliveries
):
    telemetry.set_enabled(True)
    monkeypatch.setenv(name, value)
    result = runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert result.exit_code == 1
    assert deliveries == []
    status = runner.invoke(app, ["telemetry", "status"])
    assert "Collection inactive" in status.output


def test_local_version_labels_are_not_transmitted(monkeypatch, deliveries):
    monkeypatch.setattr(telemetry, "version", lambda package: "0.21.0+private.customer")
    telemetry.set_enabled(True)
    runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert deliveries[0][1]["properties"]["caliper_version"] == "unknown"


def test_metadata_failure_does_not_replace_cli_result(monkeypatch, deliveries):
    def broken(package):
        raise RuntimeError("private metadata")

    monkeypatch.setattr(telemetry, "version", broken)
    telemetry.set_enabled(True)
    result = runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert result.exit_code == 1
    assert "File not found" in result.output
    assert deliveries == []


def test_thread_start_failure_does_not_replace_cli_result(monkeypatch, deliveries):
    def unavailable(*args):
        raise RuntimeError("cannot start new thread")

    telemetry.set_enabled(True)
    monkeypatch.setattr(telemetry.threading.Thread, "start", unavailable)
    result = runner.invoke(app, ["validate", "missing.eval.yaml"])
    assert result.exit_code == 1
    assert "File not found" in result.output
    assert "cannot start new thread" not in result.output
    assert deliveries == []


def test_failed_consent_save_preserves_previous_preference(monkeypatch, deliveries):
    def unavailable(*args):
        raise PermissionError("private config path")

    telemetry.set_enabled(True)
    monkeypatch.setattr(telemetry.os, "replace", unavailable)
    result = runner.invoke(app, ["telemetry", "disable"])
    assert result.exit_code == 2
    assert "CALIPER_TELEMETRY_DISABLED=1" in result.output
    assert "private config path" not in result.output
    home = config.config_home()
    assert json.loads((home / "telemetry.json").read_text()) == {"enabled": True}
    assert sorted(p.name for p in home.iterdir()) == ["telemetry.json"]
    assert deliveries == []


def test_stalled_delivery_does_not_hold_up_command(monkeypatch, deliveries):
    release = threading.Event()
    finished = threading.Event()

    def stalled(host, body):
        release.wait(5)
        finished.set()

    monkeypatch.setattr(telemetry, "_post", stalled)
    monkeypatch.setattr(telemetry, "DELIVERY_BUDGET_SECONDS", 0.01)
    telemetry.set_enabled(True)
    started = time.monotonic()
    try:
        result = runner.invoke(app, ["validate", "missing.eval.yaml"])
        assert result.exit_code == 1
        assert time.monotonic() - started < 1
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(1)


@pytest.mark.parametrize("response_code", [200, 302, 500])
def test_real_http_delivery_uses_fixed_headers_and_never_redirects(
    monkeypatch, response_code
):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    self.path,
                    dict(self.headers),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(response_code)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        monkeypatch.setattr(
            telemetry.http.client,
            "HTTPSConnection",
            lambda host, timeout: http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=timeout
            ),
        )
        try:
            telemetry._post("us.i.posthog.com", b'{"event":"test"}')
        finally:
            server.shutdown()
            worker.join()
    assert len(requests) == 1
    path, headers, body = requests[0]
    assert path == "/i/v0/e/"
    assert body == {"event": "test"}
    assert headers["User-Agent"] == "caliper-telemetry/1"
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


def test_offline_delivery_is_silent(monkeypatch, capsys):
    def offline(*args, **kwargs):
        raise OSError("private DNS error")

    monkeypatch.setattr(telemetry.http.client, "HTTPSConnection", offline)
    telemetry._post("us.i.posthog.com", b"{}")
    assert capsys.readouterr() == ("", "")


def test_every_product_command_has_an_explicit_telemetry_policy():
    commands = get_command(app).commands
    assert set(commands) - {"telemetry"} == {
        "run",
        "report",
        "compare",
        "list",
        "validate",
        "login",
        "logout",
    }
    assert all(
        isinstance(commands[name], telemetry.TelemetryCommand)
        for name in telemetry.COMMANDS
    )

from __future__ import annotations

import pytest
from pydantic import ValidationError

from caliper.harness.base import (
    AttemptResult,
    HarnessBackend,
    HarnessConfigurationError,
)
from caliper.judge.base import Judge, JudgeResult
from caliper.runner import run
from caliper.schema.spec import EvalSpec, McpServer, SkillConfig, TaskSpec


# --- schema validation ----------------------------------------------------


def test_mcp_block_parses_stdio_server() -> None:
    spec = EvalSpec.model_validate(
        {
            "skill": {},
            "mcp": {
                "weather": {
                    "command": "python3",
                    "args": ["server.py"],
                    "env": {"API_TOKEN": "${MCP_API_TOKEN}"},
                }
            },
            "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
        }
    )
    server = spec.mcp["weather"]
    assert isinstance(server, McpServer)
    assert server.command == "python3"
    assert server.args == ["server.py"]
    # ${VAR} is kept literal at load; it is resolved only at materialization.
    assert server.env == {"API_TOKEN": "${MCP_API_TOKEN}"}


def test_mcp_defaults_are_empty() -> None:
    spec = EvalSpec.model_validate(
        {"skill": {}, "tasks": [{"name": "t", "prompt": "p", "expect": "e"}]}
    )
    assert spec.mcp == {}


def test_mcp_server_defaults_args_and_env() -> None:
    server = McpServer.model_validate({"command": "python3"})
    assert server.args == []
    assert server.env == {}


@pytest.mark.parametrize("bad_name", ["wea ther", "we/ather", "wea.ther", ""])
def test_mcp_rejects_bad_server_name(bad_name: str) -> None:
    with pytest.raises(ValidationError, match="invalid MCP server name"):
        EvalSpec.model_validate(
            {
                "skill": {},
                "mcp": {bad_name: {"command": "python3"}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


def test_mcp_rejects_unknown_key() -> None:
    # A typo or unsupported key must error clearly (extra="forbid").
    with pytest.raises(ValidationError):
        EvalSpec.model_validate(
            {
                "skill": {},
                "mcp": {"weather": {"command": "python3", "bogus": "x"}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


# --- remote (http/sse) transport ------------------------------------------


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_mcp_block_parses_remote_server(transport: str) -> None:
    spec = EvalSpec.model_validate(
        {
            "skill": {},
            "mcp": {
                "gdrive": {
                    "type": transport,
                    "url": "https://mcp.example.com/gdrive",
                    "headers": {"Authorization": "Bearer ${GDRIVE_TOKEN}"},
                }
            },
            "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
        }
    )
    server = spec.mcp["gdrive"]
    assert server.is_remote
    assert server.type == transport
    assert server.url == "https://mcp.example.com/gdrive"
    # ${VAR} is kept literal at load; it is resolved only at materialization.
    assert server.headers == {"Authorization": "Bearer ${GDRIVE_TOKEN}"}


def test_mcp_remote_defaults_empty_headers() -> None:
    server = McpServer.model_validate({"type": "http", "url": "https://x/mcp"})
    assert server.headers == {}
    assert server.is_remote


def test_mcp_stdio_is_default_and_not_remote() -> None:
    server = McpServer.model_validate({"command": "python3"})
    assert server.type == "stdio"
    assert not server.is_remote


def test_mcp_remote_requires_url() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty url"):
        McpServer.model_validate({"type": "http"})


def test_mcp_remote_rejects_stdio_fields() -> None:
    with pytest.raises(ValidationError, match="stdio-only fields"):
        McpServer.model_validate(
            {"type": "http", "url": "https://x/mcp", "command": "python3"}
        )


def test_mcp_stdio_rejects_remote_fields() -> None:
    with pytest.raises(ValidationError, match="remote-only"):
        McpServer.model_validate({"command": "python3", "url": "https://x/mcp"})


def test_mcp_rejects_unknown_transport_type() -> None:
    with pytest.raises(ValidationError, match="invalid MCP server type"):
        McpServer.model_validate({"type": "grpc", "url": "https://x/mcp"})


def test_mcp_rejects_blank_command() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        EvalSpec.model_validate(
            {
                "skill": {},
                "mcp": {"weather": {"command": "   "}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


def test_mcp_rejects_missing_command() -> None:
    with pytest.raises(ValidationError):
        EvalSpec.model_validate(
            {
                "skill": {},
                "mcp": {"weather": {"args": ["x"]}},
                "tasks": [{"name": "t", "prompt": "p", "expect": "e"}],
            }
        )


# --- run-seam capability guard --------------------------------------------


class _NoMcpHarness(HarnessBackend):
    @property
    def name(self) -> str:
        return "nomcp"

    def run(self, *args, **kwargs) -> AttemptResult:  # pragma: no cover - never runs
        raise AssertionError("run() must not be reached when the guard fires")


class _McpHarness(HarnessBackend):
    supports_mcp = True

    def __init__(self) -> None:
        self.seen: dict | None = None

    @property
    def name(self) -> str:
        return "yesmcp"

    def run(self, *args, mcp_servers: dict | None = None, **kwargs) -> AttemptResult:
        self.seen = mcp_servers
        return AttemptResult(
            task_id=kwargs.get("task_id", "task-001"),
            attempt=kwargs.get("attempt", 1),
            transcript=[],
            final_output="ok",
            exit_code=0,
            duration_seconds=0.1,
        )


class _PassJudge(Judge):
    def evaluate(self, task, transcript, final_output, spec_dir) -> JudgeResult:
        return JudgeResult(passed=True, reasoning="ok")


def _spec_with_mcp() -> EvalSpec:
    return EvalSpec(
        skill=SkillConfig(),
        mcp={"echo": McpServer(command="python3", args=["s.py"])},
        tasks=[
            TaskSpec(id="task-001", name="t", prompt="p", assert_script="assert True")
        ],
    )


def test_guard_refuses_mcp_spec_on_unsupported_backend(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("skill: {}\ntasks: []\n")
    with pytest.raises(HarnessConfigurationError, match="does not support MCP"):
        run(
            spec=_spec_with_mcp(),
            spec_path=spec_path,
            harness=_NoMcpHarness(),
            judge=_PassJudge(),
            backend="codex",
            k=1,
            workers=1,
            timeout=30,
        )


def test_guard_allows_mcp_spec_on_supporting_backend(tmp_path) -> None:
    spec_path = tmp_path / "m.eval.yaml"
    spec_path.write_text("skill: {}\ntasks: []\n")
    harness = _McpHarness()
    run(
        spec=_spec_with_mcp(),
        spec_path=spec_path,
        harness=harness,
        judge=_PassJudge(),
        backend="claude-code",
        k=1,
        workers=1,
        timeout=30,
    )
    # The runner threads the declared McpServer models straight to the backend.
    assert harness.seen == {"echo": McpServer(command="python3", args=["s.py"])}

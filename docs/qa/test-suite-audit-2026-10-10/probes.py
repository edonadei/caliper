from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
name = sys.argv.pop(1)


def mcp_noop():
    import caliper.harness.mcp as mcp

    mcp.preflight_stdio_servers = lambda *args, **kwargs: None


def oversized_install_allowed():
    import caliper.skills as skills

    skills._MAX_FILE_BYTES = 100 * 1024 * 1024


def trigger_always_true():
    from caliper.schema.results import TaskResult

    TaskResult.trigger_only = property(lambda self: True)


def codex_without_mcp():
    from caliper.harness import get_harness

    type(get_harness("codex")).supports_mcp = False


def trigger_does_not_reset_fail_fast():
    module = importlib.import_module("caliper.runner")
    source = Path(module.__file__).read_text()
    original = "elif not record.outcome.is_execution_noise:"
    assert source.count(original) == 1
    replacement = "elif not record.outcome.is_execution_noise and record.outcome is not Outcome.NOT_CHECKED:"
    exec(
        compile(source.replace(original, replacement), module.__file__, "exec"),
        module.__dict__,
    )


def interrupt_wrong_code():
    module = importlib.import_module("caliper.commands.diagnosis")
    source = Path(module.__file__).read_text()
    original = "INTERRUPTED = 130"
    assert source.count(original) == 1
    exec(
        compile(source.replace(original, "INTERRUPTED = 129"), module.__file__, "exec"),
        module.__dict__,
    )


def cap_ignored():
    module = importlib.import_module("caliper.retry")
    source = Path(module.__file__).read_text()
    original = "if refusal is not None and refusal.kind is RefusalKind.SPENDING_CAP:"
    assert source.count(original) == 1
    exec(
        compile(source.replace(original, "if False:"), module.__file__, "exec"),
        module.__dict__,
    )


MUTATIONS = {
    "mcp-noop": mcp_noop,
    "allow-oversized": oversized_install_allowed,
    "trigger-always-true": trigger_always_true,
    "ignore-cap": cap_ignored,
    "interrupt-code-129": interrupt_wrong_code,
    "trigger-no-fail-fast-reset": trigger_does_not_reset_fail_fast,
    "codex-without-mcp": codex_without_mcp,
}
MUTATIONS[name]()
print(
    f"Applied process-local mutation {name}. No repository files changed.", flush=True
)
runpy.run_path(str(Path(__file__).with_name("guarded_pytest.py")), run_name="__main__")

"""Harness seam isolation and flat-package module boundary invariants.

See docs/adr/0044-module-boundaries-are-enforced-on-a-flat-package.md.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import ScriptedHarness
from typer.core import TyperGroup
from typer.main import get_command

from caliper.backends import BACKENDS
from caliper.environment import resolve_environment
from caliper.harness import get_harness
from caliper.harness.base import HarnessConfigurationError
from caliper.main import app
from caliper.schema.spec import load_spec
from caliper.telemetry import TelemetryCommand

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
CALIPER_DIR = ROOT / "caliper"
ADR_0044 = "docs/adr/0044-module-boundaries-are-enforced-on-a-flat-package.md"

EXPECTED_SEAM_BAN_MSG = (
    "Go through caliper.harness.get_harness; backends stay behind the harness seam."
)

# 1. Stdlib-only registry, version, and local config.
REGISTRY_AND_CONFIG = ("caliper", "caliper.backends", "caliper.config")

# 2. Eval spec and saved-run / comparison schemas.
SCHEMA_PACKAGE = "caliper.schema"
SCHEMA_ALLOWED_NON_STDLIB = (
    "pydantic",
    "yaml",
    "caliper.backends",
    "caliper.schema",
)

# 3. Pure scoring, activation, and run-comparison functions over transcripts/results.
PURE_SCORING_AND_DIFFING = (
    "caliper.compare",
    "caliper.activation",
)
PURE_SCORING_BANNED = (
    "os",
    "shutil",
    "tempfile",
    "subprocess",
    "psutil",
    "threading",
    "socket",
    "http",
    "urllib",
    "caliper.workdir",
    "caliper.runstore",
    "caliper.runner",
    "caliper.commands",
    "caliper.reporter",
    "caliper.main",
    "caliper.telemetry",
    "rich",
    "typer",
    "click",
)

# 4. Subprocess-free attempt policies over injected seams (AttemptWorkdir,
# HarnessBackend, Judge, RunStore).
ATTEMPT_POLICIES = (
    "caliper.attempt",
    "caliper.sandbox",
    "caliper.retry",
)
ATTEMPT_POLICIES_BANNED = (
    "subprocess",
    "psutil",
    "threading",
    "tempfile",
    "shutil",
    "socket",
    "http",
    "urllib",
    "caliper.runner",
    "caliper.commands",
    "caliper.reporter",
    "caliper.main",
    "caliper.telemetry",
    "rich",
    "typer",
    "click",
)

# 5. Execution, run environment, skill staging, saved-run store, harnesses, and judges.
EXECUTION_SINGLE_MODULES = (
    "caliper.runner",
    "caliper.environment",
    "caliper.workdir",
    "caliper.cancel",
    "caliper.runstore",
    "caliper.skills",
    "caliper.skillfetch",
    "caliper.skillsnapshot",
    "caliper.chatgpt",
)
EXECUTION_PACKAGES = ("caliper.harness", "caliper.judge")
EXECUTION_BANNED = (
    "caliper.commands",
    "caliper.reporter",
    "caliper.main",
    "caliper.telemetry",
    "rich",
    "typer",
    "click",
)

# 6. CLI entrypoints, Rich terminal reporter, and CLI telemetry.
CLI_SINGLE_MODULES = ("caliper.main", "caliper.reporter", "caliper.telemetry")
CLI_PACKAGE = "caliper.commands"
REPORTER_BANNED = (
    "caliper.commands",
    "caliper.runner",
    "caliper.harness",
    "typer",
)


@dataclass(frozen=True)
class _ImportEdge:
    target: str
    lineno: int
    statement: str

    @property
    def top_level(self) -> str:
        return self.target.partition(".")[0]


@dataclass(frozen=True)
class _ModuleInfo:
    name: str
    package: str
    path: Path
    tree: ast.Module
    imports: tuple[_ImportEdge, ...]

    @property
    def rel_path(self) -> str:
        return self.path.relative_to(ROOT).as_posix()


def _module_identity(path: Path) -> tuple[str, str]:
    rel = path.relative_to(ROOT)
    if path.stem == "__init__":
        pkg = ".".join(rel.parent.parts)
        return pkg, pkg
    mod = ".".join(rel.with_suffix("").parts)
    pkg = ".".join(rel.parent.parts)
    return mod, pkg


def _resolve_imports(
    tree: ast.AST,
    *,
    package: str,
    known_modules: frozenset[str],
) -> tuple[_ImportEdge, ...]:
    edges: list[_ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(
                    _ImportEdge(
                        target=alias.name,
                        lineno=node.lineno,
                        statement=f"import {alias.name}",
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            dots = "." * node.level
            raw_mod = node.module or ""
            if node.level > 0:
                base = importlib.util.resolve_name(f"{dots}{raw_mod}", package)
            else:
                base = raw_mod
            for alias in node.names:
                candidate = f"{base}.{alias.name}" if base else alias.name
                target = (
                    candidate if candidate in known_modules else (base or candidate)
                )
                stmt = f"from {dots}{raw_mod} import {alias.name}"
                edges.append(
                    _ImportEdge(target=target, lineno=node.lineno, statement=stmt)
                )
    edges.sort(key=lambda e: (e.lineno, e.target))
    return tuple(edges)


@functools.cache
def _caliper_modules() -> dict[str, _ModuleInfo]:
    paths = sorted(CALIPER_DIR.rglob("*.py"))
    identities = {path: _module_identity(path) for path in paths}
    known = frozenset(name for name, _ in identities.values())
    modules: dict[str, _ModuleInfo] = {}
    for path, (name, package) in identities.items():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = _resolve_imports(tree, package=package, known_modules=known)
        modules[name] = _ModuleInfo(
            name=name,
            package=package,
            path=path,
            tree=tree,
            imports=imports,
        )
    assert modules, "discovered no Python modules under caliper/"
    return modules


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _matches_any(module: str, prefixes: tuple[str, ...] | set[str]) -> str | None:
    for prefix in prefixes:
        if _matches_prefix(module, prefix):
            return prefix
    return None


def _registered_backend_modules() -> dict[str, tuple[str, str]]:
    parsed: dict[str, tuple[str, str]] = {}
    for name, entry in BACKENDS.items():
        mod, sep, cls = entry.harness.partition(":")
        if not (sep == ":" and mod and cls):
            raise AssertionError(
                f"BACKENDS[{name!r}].harness={entry.harness!r} must have 'module:Class' shape."
            )
        parsed[name] = (mod, cls)
    assert parsed, "BACKENDS is unexpectedly empty"
    return parsed


def _check_banned_imports(
    modules: list[_ModuleInfo],
    banned_prefixes: tuple[str, ...],
    *,
    boundary_label: str,
    remedy: str,
) -> None:
    violations: list[str] = []
    for mod in modules:
        for edge in mod.imports:
            hit = _matches_any(edge.target, banned_prefixes)
            if hit is not None:
                violations.append(
                    f"  - {mod.rel_path}:{edge.lineno}: `{edge.statement}` "
                    f"(resolves to `{edge.target}`, banned `{hit}`)"
                )
    assert not violations, (
        f"{boundary_label} violated its import boundary ({ADR_0044}):\n"
        + "\n".join(violations)
        + f"\nFix: {remedy}"
    )


def test_import_resolver_catches_relative_and_package_attribute_imports() -> None:
    known = frozenset(_caliper_modules())
    snippet = "\n".join(
        [
            "from . import claude_code",
            "from .codex import CodexHarness",
            "from ..commands import run",
            "from caliper import runner",
        ]
    )
    edges = _resolve_imports(
        ast.parse(snippet),
        package="caliper.harness",
        known_modules=known,
    )
    assert [edge.target for edge in edges] == [
        "caliper.harness.claude_code",
        "caliper.harness.codex",
        "caliper.commands.run",
        "caliper.runner",
    ]


def test_every_caliper_module_is_classified() -> None:
    modules = _caliper_modules()
    buckets: dict[str, set[str]] = {
        "REGISTRY_AND_CONFIG": set(REGISTRY_AND_CONFIG),
        "SCHEMA_PACKAGE": {m for m in modules if _matches_prefix(m, SCHEMA_PACKAGE)},
        "PURE_SCORING_AND_DIFFING": set(PURE_SCORING_AND_DIFFING),
        "ATTEMPT_POLICIES": set(ATTEMPT_POLICIES),
        "EXECUTION_AND_HARNESSES": set(EXECUTION_SINGLE_MODULES)
        | {
            m
            for m in modules
            if any(_matches_prefix(m, pkg) for pkg in EXECUTION_PACKAGES)
        },
        "CLI_AND_PRESENTATION": set(CLI_SINGLE_MODULES)
        | {m for m in modules if _matches_prefix(m, CLI_PACKAGE)},
    }

    seen: dict[str, str] = {}
    overlaps: list[str] = []
    for bucket_name, names in buckets.items():
        missing_on_disk = sorted(names - set(modules))
        assert not missing_on_disk, (
            f"{bucket_name} names modules that no longer exist on disk: {missing_on_disk}"
        )
        for mod_name in sorted(names):
            if mod_name in seen:
                overlaps.append(
                    f"{mod_name} in both {seen[mod_name]} and {bucket_name}"
                )
            seen[mod_name] = bucket_name

    assert not overlaps, f"Modules classified in multiple boundary groups: {overlaps}"

    unclassified = sorted(set(modules) - set(seen))
    assert not unclassified, (
        f"Unclassified module(s) under caliper/: {unclassified}. "
        f"Assign every new module to its import boundary in tests/test_architecture.py "
        f"({ADR_0044})."
    )


def test_every_registered_backend_is_banned_behind_harness_seam() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    banned_api: dict[str, dict[str, str]] = pyproject["tool"]["ruff"]["lint"][
        "flake8-tidy-imports"
    ]["banned-api"]
    backends = _registered_backend_modules()

    missing: list[str] = []
    wrong_msg: list[str] = []
    for backend_name, (mod, _) in backends.items():
        if mod not in banned_api:
            missing.append(f"{backend_name} ({mod})")
        elif banned_api[mod].get("msg") != EXPECTED_SEAM_BAN_MSG:
            wrong_msg.append(f"{mod}: {banned_api[mod].get('msg')!r}")

    assert not missing, (
        "Every Backend.harness module in caliper.backends.BACKENDS must be banned in "
        "pyproject.toml under [tool.ruff.lint.flake8-tidy-imports.banned-api] so "
        "callers go through caliper.harness.get_harness(). Missing:\n  "
        + "\n  ".join(missing)
    )
    assert not wrong_msg, (
        "Backend banned-api entries in pyproject.toml must use the standard seam "
        f"message {EXPECTED_SEAM_BAN_MSG!r}. Mismatched:\n  " + "\n  ".join(wrong_msg)
    )


def test_harness_package_loads_zero_registered_backends_eagerly() -> None:
    modules = _caliper_modules()
    harness_init = modules["caliper.harness"]
    backends = _registered_backend_modules()
    backend_mods = {mod for mod, _ in backends.values()}
    backend_classes = {cls for _, cls in backends.values()}

    eager_edges = [
        f"{harness_init.rel_path}:{edge.lineno} (`{edge.statement}` -> `{edge.target}`)"
        for edge in harness_init.imports
        if _matches_any(edge.target, backend_mods) is not None
    ]
    assert not eager_edges, (
        "caliper/harness/__init__.py must stay lazy and load registered backends only "
        "inside get_harness() via importlib.import_module(). Eager imports found:\n  "
        + "\n  ".join(eager_edges)
    )

    for node in harness_init.tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            exported = {
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            leaked = sorted(exported & backend_classes)
            assert not leaked, (
                "caliper/harness/__init__.py must not re-export registered backend "
                f"classes in __all__ (found {leaked}); expose get_harness() instead."
            )

    probe = (
        "import json, sys, caliper.harness; "
        "from caliper.backends import BACKENDS; "
        "mods = {b.harness.partition(':')[0] for b in BACKENDS.values()}; "
        "sys.stdout.write(json.dumps(sorted(mods & set(sys.modules))))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(proc.stdout)
    assert loaded == [], (
        "Importing `caliper.harness` in a fresh Python interpreter eagerly loaded "
        f"registered backend modules into sys.modules: {loaded}. Keep every backend "
        "lazy behind caliper.harness.get_harness()."
    )


def test_registered_backend_modules_never_import_each_other_or_leak() -> None:
    modules = _caliper_modules()
    backends = _registered_backend_modules()
    backend_mods = {mod for mod, _ in backends.values()}

    missing_files = sorted(backend_mods - set(modules))
    assert not missing_files, (
        f"BACKENDS references harness modules not found on disk: {missing_files}"
    )

    cross_imports: list[str] = []
    seam_leaks: list[str] = []
    for mod in modules.values():
        for edge in mod.imports:
            hit = _matches_any(edge.target, backend_mods)
            if hit is None or hit == mod.name:
                continue
            detail = (
                f"  - {mod.rel_path}:{edge.lineno}: `{edge.statement}` "
                f"(imports backend module `{hit}`)"
            )
            if mod.name in backend_mods:
                cross_imports.append(detail)
            else:
                seam_leaks.append(detail)

    assert not cross_imports, (
        "Registered backend modules in caliper/harness/ must stay peers and never "
        "import one another; move shared mechanics to caliper/harness/base.py, "
        "cli_config.py, history.py, mcp.py, refusal.py, or user_layer.py instead:\n"
        + "\n".join(cross_imports)
    )
    assert not seam_leaks, (
        "No module in caliper/ may statically import a registered backend module "
        "(caliper/harness/** is exempted from Ruff TID251, so this test closes the "
        "gap inside caliper/harness/ as well):\n" + "\n".join(seam_leaks)
    )


def test_judge_and_internal_harness_helpers_stay_behind_seams() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    banned_api: dict[str, dict[str, str]] = pyproject["tool"]["ruff"]["lint"][
        "flake8-tidy-imports"
    ]["banned-api"]

    expected_bans = {
        "caliper.judge.eval_judge": (
            "Go through caliper.judge; keep concrete judge internals behind the judge seam."
        ),
        "caliper.harness.cli_config": (
            "Internal to caliper.harness; expose shared contracts through caliper.harness.base if needed outside harnesses."
        ),
        "caliper.harness.history": (
            "Internal to caliper.harness; expose shared contracts through caliper.harness.base if needed outside harnesses."
        ),
        "caliper.harness.user_layer": (
            "Internal to caliper.harness; expose shared contracts through caliper.harness.base if needed outside harnesses."
        ),
    }

    missing_or_wrong: list[str] = []
    for mod, expected_msg in expected_bans.items():
        if mod not in banned_api:
            missing_or_wrong.append(f"Missing banned-api entry for {mod}")
        elif banned_api[mod].get("msg") != expected_msg:
            missing_or_wrong.append(
                f"{mod}: got {banned_api[mod].get('msg')!r}, expected {expected_msg!r}"
            )
    assert not missing_or_wrong, (
        "pyproject.toml [tool.ruff.lint.flake8-tidy-imports.banned-api] must ban internal helpers:\n  "
        + "\n  ".join(missing_or_wrong)
    )

    modules = _caliper_modules()
    judge_banned = "caliper.judge.eval_judge"
    harness_banned = (
        "caliper.harness.cli_config",
        "caliper.harness.history",
        "caliper.harness.user_layer",
    )

    violations: list[str] = []
    for mod in modules.values():
        is_in_judge = mod.name == "caliper.judge" or mod.name.startswith(
            "caliper.judge."
        )
        is_in_harness = mod.name == "caliper.harness" or mod.name.startswith(
            "caliper.harness."
        )
        for edge in mod.imports:
            if not is_in_judge and _matches_prefix(edge.target, judge_banned):
                violations.append(
                    f"  - {mod.rel_path}:{edge.lineno}: `{edge.statement}` "
                    f"(imports `{edge.target}` outside caliper.judge)"
                )
            if not is_in_harness and _matches_any(edge.target, harness_banned):
                violations.append(
                    f"  - {mod.rel_path}:{edge.lineno}: `{edge.statement}` "
                    f"(imports `{edge.target}` outside caliper.harness)"
                )

    assert not violations, (
        "Modules outside caliper.judge must not import caliper.judge.eval_judge, and "
        "modules outside caliper.harness must not import internal harness helpers:\n"
        + "\n".join(violations)
    )


def test_history_refusal_hints_list_every_history_capable_backend(
    tmp_path: Path,
) -> None:
    history_capable = {name for name in BACKENDS if get_harness(name).supports_history}
    assert history_capable, "expected at least one backend with supports_history=True"

    (tmp_path / "h.history.yaml").write_text("- user: hi\n- assistant: hello\n")
    spec_path = tmp_path / "s.eval.yaml"
    spec_path.write_text(
        "tasks:\n  - name: t\n    history: ./h.history.yaml\n    prompt: p\n    expect: x\n"
    )
    spec = load_spec(spec_path)

    with pytest.raises(HarnessConfigurationError) as generic_exc:
        resolve_environment(
            spec,
            spec_path,
            harness=ScriptedHarness(name="stub-no-history", supports_history=False),
            ablate=[],
            user_customizations=None,
            timeout=30,
        )
    generic_hint = str(generic_exc.value).split("\n\n", 1)[1]
    generic_flags = set(re.findall(r"--model\s+([a-z0-9_-]+)", generic_hint))
    generic_quoted = set(re.findall(r"'([a-z0-9_-]+)'", generic_hint))
    assert generic_flags == history_capable, (
        "Generic history refusal hint in caliper/environment.py is out of sync with "
        "BACKENDS whose harness has supports_history=True.\n"
        f"  Expected --model flags: {sorted(history_capable)}\n"
        f"  Found in environment.py: {sorted(generic_flags)}"
    )
    assert generic_quoted == history_capable, (
        "Quoted backend names in caliper/environment.py's history refusal hint are "
        "out of sync with BACKENDS whose harness has supports_history=True.\n"
        f"  Expected quoted names: {sorted(history_capable)}\n"
        f"  Found in environment.py: {sorted(generic_quoted)}"
    )

    non_history_backends = [
        name for name in BACKENDS if not get_harness(name).supports_history
    ]
    assert "antigravity" in non_history_backends, (
        "Expected 'antigravity' to be exercised here unless it gained supports_history"
    )
    for backend_name in non_history_backends:
        harness = get_harness(backend_name)
        with pytest.raises(HarnessConfigurationError) as backend_exc:
            resolve_environment(
                spec,
                spec_path,
                harness=harness,
                ablate=[],
                user_customizations=None,
                timeout=30,
            )
        hint_text = str(backend_exc.value).split("\n\n", 1)[1]
        hint_flags = set(re.findall(r"--model\s+([a-z0-9_-]+)", hint_text))
        assert hint_flags == history_capable, (
            f"History refusal hint for backend {backend_name!r} "
            f"({type(harness).__module__}.{type(harness).__name__}.history_unsupported_hint) "
            "is out of sync with BACKENDS whose harness has supports_history=True.\n"
            f"  Expected --model flags: {sorted(history_capable)}\n"
            f"  Found in hint: {sorted(hint_flags)}"
        )


def test_registry_and_schema_imports_remain_minimal() -> None:
    modules = _caliper_modules()

    stdlib_violations: list[str] = []
    for mod_name in REGISTRY_AND_CONFIG:
        mod = modules[mod_name]
        for edge in mod.imports:
            if edge.top_level not in sys.stdlib_module_names:
                stdlib_violations.append(
                    f"  - {mod.rel_path}:{edge.lineno}: `{edge.statement}` "
                    f"(non-stdlib `{edge.target}`)"
                )
    assert not stdlib_violations, (
        "Registry and config modules (`caliper`, `caliper.backends`, `caliper.config`) "
        f"must import Python stdlib only ({ADR_0044}):\n" + "\n".join(stdlib_violations)
    )

    schema_mods = [
        mod for name, mod in modules.items() if _matches_prefix(name, SCHEMA_PACKAGE)
    ]
    assert {"caliper.schema.spec", "caliper.schema.results"} <= {
        m.name for m in schema_mods
    }
    schema_violations: list[str] = []
    for mod in schema_mods:
        for edge in mod.imports:
            if edge.top_level in sys.stdlib_module_names:
                continue
            if _matches_any(edge.target, SCHEMA_ALLOWED_NON_STDLIB) is not None:
                continue
            schema_violations.append(
                f"  - {mod.rel_path}:{edge.lineno}: `{edge.statement}` "
                f"(disallowed `{edge.target}`)"
            )
    assert not schema_violations, (
        "`caliper.schema.*` modules may import only stdlib, `pydantic`, `yaml`, "
        f"`caliper.backends`, and sibling `caliper.schema.*` modules ({ADR_0044}):\n"
        + "\n".join(schema_violations)
    )


def test_pure_scoring_and_diffing_stays_io_and_process_free() -> None:
    modules = _caliper_modules()
    _check_banned_imports(
        [modules[name] for name in PURE_SCORING_AND_DIFFING],
        PURE_SCORING_BANNED,
        boundary_label="Pure scoring and diffing (`compare`, `activation`)",
        remedy=(
            "Keep `compare` and `activation` as pure functions over transcripts and "
            "saved-run schemas; filesystem, workdir, subprocess, runner, and CLI work "
            "belongs in higher boundaries."
        ),
    )


def test_attempt_policies_stay_subprocess_runner_and_cli_free() -> None:
    modules = _caliper_modules()
    _check_banned_imports(
        [modules[name] for name in ATTEMPT_POLICIES],
        ATTEMPT_POLICIES_BANNED,
        boundary_label="Attempt policies (`attempt`, `sandbox`, `retry`)",
        remedy=(
            "Drive execution through injected seams (`AttemptWorkdir`, `HarnessBackend`, "
            "`Judge`, `RunStore`) rather than spawning subprocesses/threads or importing "
            "`caliper.runner` or CLI/Rich modules."
        ),
    )


def test_execution_harnesses_and_judges_never_import_cli_or_ui() -> None:
    modules = _caliper_modules()
    exec_mods = [modules[name] for name in EXECUTION_SINGLE_MODULES]
    for pkg in EXECUTION_PACKAGES:
        pkg_mods = [mod for name, mod in modules.items() if _matches_prefix(name, pkg)]
        assert pkg_mods, f"Found no modules under package {pkg}"
        exec_mods.extend(pkg_mods)

    _check_banned_imports(
        exec_mods,
        EXECUTION_BANNED,
        boundary_label="Execution, harnesses, and judges",
        remedy=(
            "Return typed results or raise domain exceptions mapped in "
            "`caliper/commands/diagnosis.py`; keep Rich, Typer, Click, `reporter`, "
            "and `telemetry` at the CLI edge."
        ),
    )


def test_reporter_stays_decoupled_from_commands_runner_harness_and_typer() -> None:
    modules = _caliper_modules()
    _check_banned_imports(
        [modules["caliper.reporter"]],
        REPORTER_BANNED,
        boundary_label="Terminal reporter (`caliper.reporter`)",
        remedy=(
            "`caliper.reporter` renders `caliper.schema.results` models with Rich; "
            "keep Typer CLI wiring in `caliper.commands.*` and execution in "
            "`caliper.runner` / `caliper.harness`."
        ),
    )


def test_every_main_app_command_uses_telemetry_command() -> None:
    main_mod = _caliper_modules()["caliper.main"]

    bare_decorators = [
        f"{main_mod.rel_path}:{dec.lineno}: `@app.command` without `(cls=TelemetryCommand)`"
        for node in ast.walk(main_mod.tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for dec in node.decorator_list
        if isinstance(dec, ast.Attribute)
        and isinstance(dec.value, ast.Name)
        and dec.value.id == "app"
        and dec.attr == "command"
    ]
    assert not bare_decorators, "\n".join(bare_decorators)

    command_calls = [
        node
        for node in ast.walk(main_mod.tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "app"
        and node.func.attr == "command"
    ]
    assert command_calls, "found no `app.command(...)` registrations in caliper/main.py"

    missing_cls: list[str] = []
    for call in command_calls:
        cmd_label = (
            repr(call.args[0].value)
            if call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            else "<unnamed>"
        )
        cls_kw = next((kw for kw in call.keywords if kw.arg == "cls"), None)
        is_telemetry_cls = cls_kw is not None and (
            (
                isinstance(cls_kw.value, ast.Name)
                and cls_kw.value.id == "TelemetryCommand"
            )
            or (
                isinstance(cls_kw.value, ast.Attribute)
                and cls_kw.value.attr == "TelemetryCommand"
            )
        )
        if not is_telemetry_cls:
            missing_cls.append(
                f"  - {main_mod.rel_path}:{call.lineno}: app.command({cmd_label}, ...) "
                "missing `cls=TelemetryCommand`"
            )

    assert not missing_cls, (
        "Every `app.command(...)` registration in caliper/main.py must specify "
        "`cls=TelemetryCommand` so CLI telemetry wraps all top-level commands:\n"
        + "\n".join(missing_cls)
    )

    click_group = get_command(app)
    unwrapped = [
        name
        for name, cmd in click_group.commands.items()
        if not isinstance(cmd, TyperGroup) and not isinstance(cmd, TelemetryCommand)
    ]
    assert not unwrapped, (
        f"Top-level CLI commands registered without TelemetryCommand: {unwrapped}"
    )


def test_pytest_config_isolates_discovery() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ini = pyproject["tool"]["pytest"]["ini_options"]
    assert ini["testpaths"] == ["tests"]
    patterns = ini["norecursedirs"]
    for excluded in (
        ".claude",
        ".caliper",
        ".codex",
        ".git",
        ".venv",
        "build",
        "dist",
        "foo.egg",
        "node_modules",
        "venv",
        "worktrees",
    ):
        assert any(fnmatch.fnmatchcase(excluded, pat) for pat in patterns), (
            f"{excluded!r} is not excluded by [tool.pytest.ini_options].norecursedirs "
            f"({patterns!r}); explicit `pytest .` runs would recurse into it."
        )


def test_ruff_lint_selects_agent_guardrail_rules() -> None:
    """Ruff lint configuration must enforce agent guardrail rule families.

    See docs/adr/0002-ruff-as-sole-formatting-authority.md.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lint = pyproject["tool"]["ruff"]["lint"]
    selected = set(lint.get("select", []))
    required = {
        "B",
        "BLE",
        "C4",
        "E4",
        "E7",
        "E9",
        "F",
        "FURB",
        "I",
        "PIE",
        "PT",
        "PTH",
        "RUF",
        "SIM",
        "T20",
        "TID",
        "UP",
    }
    missing = required - selected
    assert not missing, (
        f"[tool.ruff.lint].select in pyproject.toml is missing required rule families: {sorted(missing)}"
    )
    assert "E501" not in selected, (
        "E501 must stay off: the formatter owns line length (see docs/adr/0002-ruff-as-sole-formatting-authority.md)."
    )

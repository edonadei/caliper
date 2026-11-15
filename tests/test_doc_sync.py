"""Docs, decision records, and version pins that must stay in sync with the code.

See AGENTS.md -> Updating docs after API changes, Formatting, and Decision docs.
"""

from __future__ import annotations

import fnmatch
import functools
import os
import re
import subprocess
import sys
import typing
from collections import defaultdict
from pathlib import Path

import yaml
from pydantic import BaseModel
from typer.main import get_command

from caliper.backends import VALID_BACKENDS
from caliper.commands.diagnosis import ExitCode
from caliper.main import app
from caliper.schema.results import Outcome, RunResults
from caliper.schema.spec import EvalSpec

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

# Assigned by the loader from the task's position, never written in a spec.
_LOADER_KEYS = {"id"}

_ADR_FILENAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
_RUFF_TEXT_PIN = re.compile(r"ruff(?:==|@)(\d+\.\d+\.\d+)")


@functools.cache
def _pyproject() -> dict[str, typing.Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _markdown_section(rel_path: str, heading: str) -> str:
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    assert heading in text, f"missing heading {heading!r} in {rel_path}"
    return text.split(heading, 1)[1].split("\n#", 1)[0]


def _pydantic_models(root: type[BaseModel]) -> list[type[BaseModel]]:
    seen: list[type[BaseModel]] = []

    def _walk(model: type[BaseModel]) -> None:
        if model in seen:
            return
        seen.append(model)
        for field in model.model_fields.values():
            pending = [field.annotation]
            while pending:
                annotation = pending.pop()
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    _walk(annotation)
                pending.extend(typing.get_args(annotation))

    _walk(root)
    return seen


def _run_flags() -> dict[str, set[str]]:
    """Each `caliper run` option's primary flag, mapped to every spelling of it."""
    run = get_command(app).commands["run"]
    flags = {
        param.opts[0]: set(param.opts) | set(param.secondary_opts)
        for param in run.params
        if param.param_type_name == "option" and param.opts[0].startswith("--")
    }
    # An empty result would make every check below pass vacuously.
    assert flags, "found no `caliper run` options; did typer change its types?"
    return flags


def _mentions_flag(text: str, flag: str) -> bool:
    return re.search(re.escape(flag) + r"(?![\w-])", text) is not None


def test_readme_cli_reference_lists_every_subcommand() -> None:
    section = _markdown_section("README.md", "## CLI reference")
    documented = set(re.findall(r"\|\s*`caliper\s+([a-z][\w-]*)", section))
    assert documented, "found no `caliper <cmd>` rows in README.md's CLI reference"
    registered = set(get_command(app).commands)
    assert documented == registered, (
        f"README.md CLI reference {sorted(documented)} != registered commands "
        f"{sorted(registered)}"
    )


def test_readme_run_flags_table_lists_every_run_flag() -> None:
    table = _markdown_section("README.md", "### `caliper run` flags")
    missing = [
        flag
        for flag, spellings in _run_flags().items()
        if not any(_mentions_flag(table, s) for s in spellings)
    ]
    assert missing == [], f"add to README.md's `caliper run` flags table: {missing}"


def test_readme_run_flags_table_has_no_stale_flags() -> None:
    table = _markdown_section("README.md", "### `caliper run` flags")
    known = set().union(*_run_flags().values())
    documented = set(re.findall(r"--[a-z][\w-]*", table))
    assert documented - known == set(), "flags `caliper run` no longer has"


def test_skill_references_mention_every_run_flag() -> None:
    missing_by_doc: dict[str, list[str]] = {}
    for rel_path in (
        "skills/evaluate-skill/REFERENCE.md",
        "skills/grill-skill/REFERENCE.md",
    ):
        reference = (ROOT / rel_path).read_text(encoding="utf-8")
        missing = [
            flag
            for flag, spellings in _run_flags().items()
            if not any(_mentions_flag(reference, s) for s in spellings)
        ]
        if missing:
            missing_by_doc[rel_path] = missing
    assert missing_by_doc == {}, f"document every `caliper run` flag: {missing_by_doc}"


def test_spec_reference_documents_every_spec_key() -> None:
    reference = (ROOT / "docs/spec-reference.md").read_text(encoding="utf-8")
    missing = [
        f"{model.__name__}.{key}"
        for model in _pydantic_models(EvalSpec)
        for name, field in model.model_fields.items()
        if (key := field.alias or name) not in _LOADER_KEYS
        and not re.search(rf"(?<![\w-]){re.escape(key)}:|`{re.escape(key)}`", reference)
    ]
    assert missing == [], f"document in docs/spec-reference.md: {missing}"


def test_results_doc_documents_outcomes_and_schema_fields() -> None:
    scoring_section = _markdown_section("docs/results.md", "## Scoring")
    documented_outcomes = set(
        re.findall(r"^\|\s*`([a-z_]+)`\s*\|", scoring_section, re.MULTILINE)
    )
    expected_outcomes = {outcome.value for outcome in Outcome}
    assert documented_outcomes == expected_outcomes, (
        f"docs/results.md Outcome table {sorted(documented_outcomes)} != "
        f"Outcome enum {sorted(expected_outcomes)}"
    )

    results_doc = (ROOT / "docs/results.md").read_text(encoding="utf-8")
    missing_fields = [
        f"{model.__name__}.{key}"
        for model in _pydantic_models(RunResults)
        for key in (*model.model_fields, *model.model_computed_fields)
        if not re.search(rf"`[^`\n]*\b{re.escape(key)}\b[^`\n]*`", results_doc)
    ]
    assert missing_fields == [], (
        f"document RunResults schema fields in docs/results.md: {missing_fields}"
    )


def _adr_index() -> tuple[list[Path], set[str], dict[str, list[str]]]:
    adr_dir = ROOT / "docs/adr"
    entries = sorted(adr_dir.iterdir())
    assert entries, "found no ADR files in docs/adr/"
    by_number: dict[str, list[str]] = defaultdict(list)
    adr_names: set[str] = set()
    for entry in entries:
        match = _ADR_FILENAME.match(entry.name) if entry.is_file() else None
        if match is not None:
            adr_names.add(entry.name)
            by_number[match.group(1)].append(entry.name)
    return entries, adr_names, by_number


def test_adr_numbers_are_unique_and_headers_match() -> None:
    entries, adr_names, by_number = _adr_index()
    bad_names = [entry.name for entry in entries if entry.name not in adr_names]
    bad_headers: list[str] = []

    for entry in entries:
        if entry.name not in adr_names:
            continue
        lines = entry.read_text(encoding="utf-8").splitlines()
        first_line = lines[0] if lines else ""
        if not re.match(r"^# [^#\s]", first_line) or re.match(
            r"^# (?:ADR[- ]?)?\d{4}[.: ]", first_line
        ):
            bad_headers.append(f"{entry.name}: {first_line!r}")

    assert bad_names == [], (
        f"every file in docs/adr/ must match NNNN-slug.md: {bad_names}"
    )
    duplicates = {num: names for num, names in by_number.items() if len(names) > 1}
    assert duplicates == {}, (
        "duplicate ADR number prefixes in docs/adr/ "
        f"(renumber after the highest existing ADR): {duplicates}"
    )
    assert bad_headers == [], (
        "line 1 of every ADR in docs/adr/ must be '# <Decision title>' "
        f"(no number prefix): {bad_headers}"
    )


def test_adr_references_resolve() -> None:
    adr_dir = (ROOT / "docs/adr").resolve()
    _, adr_names, by_number = _adr_index()

    scan_paths = sorted(
        {
            *ROOT.glob("caliper/**/*.py"),
            *ROOT.glob("docs/**/*.md"),
            *(
                p
                for p in ROOT.glob("skills/**/*")
                if p.is_file() and p.suffix in {".py", ".md", ".yaml", ".yml"}
            ),
            *ROOT.glob(".github/**/*.md"),
            ROOT / "README.md",
            ROOT / "AGENTS.md",
        }
    )

    broken: list[str] = []
    checked = 0
    docs_adr_pat = re.compile(r"docs/adr/(\d{4}(?:-[a-z0-9-]+(?:\.md)?)?)(?![\w-])")
    md_link_pat = re.compile(r"\]\(([^)\s]+)\)")

    for path in scan_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()

        for match in docs_adr_pat.finditer(text):
            checked += 1
            ref = match.group(1)
            line_no = text.count("\n", 0, match.start()) + 1
            if len(ref) == 4:
                if len(by_number.get(ref, [])) != 1:
                    broken.append(f"{rel}:{line_no} -> docs/adr/{ref}")
            else:
                expected = ref if ref.endswith(".md") else f"{ref}.md"
                if expected not in adr_names:
                    broken.append(f"{rel}:{line_no} -> docs/adr/{ref}")

        for match in md_link_pat.finditer(text):
            raw_target = match.group(1)
            target = raw_target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if "adr/" not in target and not re.match(r"^\d{4}-", Path(target).name):
                continue
            checked += 1
            line_no = text.count("\n", 0, match.start()) + 1
            resolved = (path.parent / target).resolve()
            if target.endswith("/"):
                if resolved != adr_dir:
                    broken.append(f"{rel}:{line_no} -> ({raw_target})")
            elif resolved.parent != adr_dir or resolved.name not in adr_names:
                broken.append(f"{rel}:{line_no} -> ({raw_target})")

    assert checked > 0, "found no ADR references to validate"
    assert broken == [], f"broken ADR references (must resolve in docs/adr/): {broken}"


def _heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "").lower()).strip()


def test_context_glossary_wiki_links_resolve() -> None:
    context = (ROOT / "docs/CONTEXT.md").read_text(encoding="utf-8")

    wrapped = [
        f"docs/CONTEXT.md:{context.count(chr(10), 0, m.start()) + 1}: {m.group(0)!r}"
        for m in re.finditer(r"\[\[[^\]]*\n[^\]]*\]\]", context)
    ]
    assert wrapped == [], (
        f"keep [[wiki]] links on a single line in docs/CONTEXT.md: {wrapped}"
    )

    headings = {
        _heading_key(m.group(1))
        for m in re.finditer(r"^## (.+)$", context, re.MULTILINE)
    }
    assert headings, "found no `## ` headings in docs/CONTEXT.md"

    links = list(re.finditer(r"\[\[([^\]]+)\]\]", context))
    assert links, "found no `[[wiki]]` links in docs/CONTEXT.md"

    unresolved: list[str] = []
    for match in links:
        target = match.group(1).split("|", 1)[0]
        key = _heading_key(target)
        if key not in headings:
            line_no = context.count("\n", 0, match.start()) + 1
            unresolved.append(
                f"docs/CONTEXT.md:{line_no}: {match.group(0)} (key {key!r})"
            )

    assert unresolved == [], (
        "every [[wiki]] link in docs/CONTEXT.md must match a `## ` heading "
        "(link to ADRs with `[ADR NNNN](adr/NNNN-slug.md)` instead): "
        f"{unresolved}"
    )


def test_ruff_version_pin_in_lockstep() -> None:
    dev_deps: list[str] = _pyproject()["project"]["optional-dependencies"]["dev"]
    pyproject_pins = [
        m.group(1)
        for dep in dev_deps
        if (m := re.fullmatch(r"ruff==(\d+\.\d+\.\d+)", dep.strip()))
    ]
    assert len(pyproject_pins) == 1, (
        f"expected exactly one ruff==X.Y.Z pin in pyproject.toml dev deps: {dev_deps}"
    )

    pre_commit_raw = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    pre_commit = yaml.safe_load(pre_commit_raw)
    ruff_repos = [
        repo
        for repo in pre_commit.get("repos", [])
        if str(repo.get("repo", "")).rstrip("/").endswith("/ruff-pre-commit")
    ]
    assert len(ruff_repos) == 1, (
        f"expected one ruff-pre-commit entry in .pre-commit-config.yaml: {ruff_repos}"
    )
    rev = str(ruff_repos[0].get("rev", ""))
    assert re.fullmatch(r"v\d+\.\d+\.\d+", rev), (
        f"ruff-pre-commit rev must be vX.Y.Z, got {rev!r}"
    )
    pre_commit_pins = sorted({rev.lstrip("v"), *_RUFF_TEXT_PIN.findall(pre_commit_raw)})

    pins: dict[str, list[str]] = {
        "pyproject.toml": pyproject_pins,
        ".pre-commit-config.yaml": pre_commit_pins,
    }
    for rel_path in (".github/CONTRIBUTING.md", "AGENTS.md"):
        found = sorted(
            set(_RUFF_TEXT_PIN.findall((ROOT / rel_path).read_text("utf-8")))
        )
        assert found, f"no ruff==X.Y.Z or ruff@X.Y.Z pin found in {rel_path}"
        pins[rel_path] = found

    all_versions = {version for versions in pins.values() for version in versions}
    assert len(all_versions) == 1, f"keep ruff version pins in lockstep: {pins}"


def test_python_versions_in_lockstep() -> None:
    pyproject = _pyproject()
    requires_python: str = pyproject["project"]["requires-python"]
    floor_match = re.fullmatch(r">=(\d+\.\d+)", requires_python.strip())
    assert floor_match is not None, (
        f"expected requires-python = '>=3.X' in pyproject.toml, got {requires_python!r}"
    )
    floor = floor_match.group(1)

    classifiers: list[str] = pyproject["project"]["classifiers"]
    classified_versions = [
        m.group(1)
        for entry in classifiers
        if (m := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", entry))
    ]
    assert classified_versions, "found no Python 3.X classifiers in pyproject.toml"

    ruff_target: str = pyproject["tool"]["ruff"]["target-version"]
    expected_ruff_target = f"py{floor.replace('.', '')}"
    assert ruff_target == expected_ruff_target, (
        f"[tool.ruff].target-version={ruff_target!r} != requires-python floor "
        f"{expected_ruff_target!r}"
    )

    ci = yaml.safe_load(
        (ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    )
    ci_matrix = [
        str(v) for v in ci["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    ]
    assert classified_versions[0] == floor, (
        f"lowest Python classifier {classified_versions[0]!r} != "
        f"requires-python floor {floor!r}"
    )
    assert classified_versions == ci_matrix, (
        f"pyproject.toml Python classifiers {classified_versions} != "
        f".github/workflows/lint.yml test matrix {ci_matrix}"
    )


def test_valid_backends_are_documented() -> None:
    assert VALID_BACKENDS, "VALID_BACKENDS is empty"
    required_docs = {
        "README.md",
        "docs/backends.md",
        "skills/evaluate-skill/REFERENCE.md",
        "skills/grill-skill/REFERENCE.md",
    }
    for ref_path in sorted(ROOT.glob("skills/*/REFERENCE.md")):
        rel = ref_path.relative_to(ROOT).as_posix()
        text = ref_path.read_text(encoding="utf-8")
        if any(f"`{backend}`" in text for backend in VALID_BACKENDS):
            required_docs.add(rel)

    missing: dict[str, list[str]] = {}
    for rel_path in sorted(required_docs):
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        absent = [
            backend for backend in sorted(VALID_BACKENDS) if f"`{backend}`" not in text
        ]
        if absent:
            missing[rel_path] = absent

    assert missing == {}, f"document every backend in VALID_BACKENDS: {missing}"


def test_exit_codes_match_enum() -> None:
    readme_section = _markdown_section("README.md", "### Exit codes")
    documented = [
        int(code)
        for code in re.findall(r"^\|\s*`(\d+)`\s*\|", readme_section, re.MULTILINE)
    ]
    assert documented, "found no exit codes in README.md's `### Exit codes` table"
    expected = {int(code) for code in ExitCode}
    assert set(documented) == expected, (
        f"README.md exit codes {sorted(set(documented))} != ExitCode {sorted(expected)}"
    )

    detail_section = _markdown_section("docs/results.md", "## Exit codes in detail")
    assert "README.md#exit-codes" in detail_section, (
        "docs/results.md `## Exit codes in detail` must link to README.md#exit-codes"
    )
    cited_codes = {int(code) for code in re.findall(r"`(\d+)`", detail_section)}
    assert cited_codes, (
        "found no exit codes in docs/results.md `## Exit codes in detail`"
    )
    assert cited_codes <= expected, (
        f"docs/results.md cites unknown exit codes {sorted(cited_codes - expected)}"
    )


@functools.cache
def _readme_indexed_docs() -> set[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return set(re.findall(r"\b(docs/[A-Za-z0-9_-]+\.md)\b", readme))


def _is_approved_markdown(rel: Path) -> bool:
    posix = rel.as_posix()
    if posix in {"README.md", "AGENTS.md", "examples/README.md"}:
        return True
    parts = rel.parts
    if parts[0] == ".github":
        return True
    if len(parts) == 2 and parts[0] == "docs":
        return posix in _readme_indexed_docs()
    if len(parts) == 3 and parts[:2] == ("docs", "adr"):
        return _ADR_FILENAME.match(parts[2]) is not None
    if len(parts) == 3 and parts[0] == "examples":
        return parts[2] == "SKILL.md"
    if len(parts) == 3 and parts[0] == "skills":
        return parts[2] in {"SKILL.md", "REFERENCE.md"}
    return False


def _is_pruned_dir(name: str, norecursedirs: list[str]) -> bool:
    if name == ".github":
        return False
    if name.endswith(".egg-info"):
        return True
    return any(fnmatch.fnmatchcase(name, pat) for pat in norecursedirs)


def _repo_markdown_files() -> list[Path]:
    norecursedirs: list[str] = _pyproject()["tool"]["pytest"]["ini_options"][
        "norecursedirs"
    ]
    if (ROOT / ".git").exists():
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            found: list[Path] = []
            for entry in proc.stdout.split("\0"):
                if not entry or not entry.endswith(".md"):
                    continue
                rel = Path(entry)
                if any(_is_pruned_dir(part, norecursedirs) for part in rel.parts[:-1]):
                    continue
                if (ROOT / rel).is_file():
                    found.append(rel)
            return sorted(set(found))

    walked: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not _is_pruned_dir(d, norecursedirs)]
        for filename in sorted(filenames):
            if filename.endswith(".md"):
                walked.append((Path(dirpath) / filename).relative_to(ROOT))
    return sorted(walked)


def test_no_unsolicited_markdown_files() -> None:
    files = _repo_markdown_files()
    assert files, "found no markdown files in repository"
    unsolicited = [rel.as_posix() for rel in files if not _is_approved_markdown(rel)]
    assert unsolicited == [], (
        "unsolicited markdown files found (allowed: README.md, AGENTS.md, "
        ".github/**/*.md, docs/*.md linked from README.md, docs/adr/NNNN-*.md, "
        "examples/README.md, examples/*/SKILL.md, skills/*/{SKILL,REFERENCE}.md): "
        f"{unsolicited}"
    )

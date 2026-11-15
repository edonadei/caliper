"""Docs, decision records, and version pins that must stay in sync with the code.

See AGENTS.md -> Updating docs after API changes, Formatting, and Decision docs.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import json
import os
import re
import subprocess
import sys
import tokenize
import typing
from collections import Counter, defaultdict
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
_TY_TEXT_PIN = re.compile(r"\bty(?:==|@)(\d+\.\d+\.\d+)")


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


# `_Avoid_:` terms that also name something else the glossary keeps, so a
# match is not a misuse. Every other term is banned wherever it is checked.
_CONTEXT_DEPENDENT_AVOID_TERMS = {
    "baseline": "hillclimb-skill's baseline runs are a different concept",
    "codex login": "the Codex CLI's own login command",
    "connectors": "hosted connectors are one kind of user customization",
    "extensions": "pi extensions are real",
    "firing": "avoided only for the observation, not the event",
    "hook": "`setup:` and `cleanup:` are hooks",
    "inject": "codex injects a mentioned skill, and prompt injection is its own",
    "invocation": "avoided only for the observation, not the event",
    "profile": "hermes' own word for something narrower",
    "sandbox": "an entry of its own, avoided only for the three it is not",
    "script": "a step may run a script",
    "setup": "the `setup:` step",
    "spec dir": "the spec's own directory, avoided only as a name for the workdir",
    "triggering": "avoided only for the observation, not the event",
}

# Where an avoided term may stand: the glossary defines them, ADRs record the
# vocabulary of their time, and a migration guide names the old terms.
_AVOID_TERM_EXEMPT_DOCS = ("docs/CONTEXT.md", "docs/adr/*", "docs/MIGRATING-*.md")

# Code that names the era a pre-#18 saved run carries, in that era's words.
_AVOID_TERM_HISTORICAL_USES = {
    "caliper/compare.py": {"force-load"},
    "caliper/schema/results.py": {"force-load"},
}

_STRING_BODY = re.compile(r"[rRbBuUfF]*('''|\"\"\"|'|\")(.*)\1", re.DOTALL)


def _without_parentheticals(text: str) -> str:
    kept: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            kept.append(char)
    return "".join(kept)


def _glossary_avoid_terms() -> dict[str, list[str]]:
    """Each `_Avoid_:` term in docs/CONTEXT.md, mapped to the entries avoiding it."""
    context = (ROOT / "docs/CONTEXT.md").read_text(encoding="utf-8")
    terms: defaultdict[str, list[str]] = defaultdict(list)
    heading = ""
    for block in re.split(r"\n\s*\n", context):
        if found := re.findall(r"^## (.+)$", block, re.MULTILINE):
            heading = found[-1]
        avoid = re.search(r"^_Avoid_:(.*)", block, re.MULTILINE | re.DOTALL)
        if avoid is None:
            continue
        # A parenthetical says when or why, and may hold commas, links and
        # parentheticals of its own.
        for term in _without_parentheticals(avoid.group(1)).split(","):
            if term := " ".join(term.split()).strip(" .`").lower():
                terms[term].append(heading)
    return dict(terms)


def _python_prose(path: Path) -> list[tuple[int, str]]:
    """The comments and string literals of a module, each run with its first line.

    Code is left out, so a match never spans two statements. A run of comment
    lines, or of implicitly concatenated literals, is one piece, so a term split
    across it still matches. An escape such as ``\\n`` reads as a space.
    """
    strings = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    # Tokens that may sit inside a run without ending it.
    within_run = {
        tokenize.NL,
        getattr(tokenize, "FSTRING_START", -1),
        getattr(tokenize, "FSTRING_END", -1),
    }
    pieces: list[tuple[int, str]] = []
    run_kind: int | None = None
    run_end = -1
    with path.open("rb") as source:
        for tok in tokenize.tokenize(source.readline):
            if tok.type in within_run:
                continue
            if tok.type == tokenize.COMMENT:
                text = tok.string.lstrip("#")
                joins = run_kind == tokenize.COMMENT and run_end == tok.start[0] - 1
                run_kind = tokenize.COMMENT
            elif tok.type in strings:
                body = tok.string
                if quoted := _STRING_BODY.fullmatch(body):
                    body = quoted.group(2)
                text = re.sub(r"\\.", " ", body)
                joins = run_kind == tokenize.STRING
                run_kind = tokenize.STRING
            else:
                run_kind = None
                continue
            if joins:
                gap = "\n" * (tok.start[0] - run_end)
                pieces[-1] = (pieces[-1][0], pieces[-1][1] + gap + text)
            else:
                pieces.append((tok.start[0], text))
            run_end = tok.end[0]
    return pieces


def _avoid_term_pattern(term: str) -> re.Pattern[str]:
    words = (re.escape(word) for word in re.split(r"[\s-]+", term))
    # Hyphens and line breaks count as spaces, so a wrapped or hyphenated
    # spelling still matches, as does an inflection like `injected`, but an
    # identifier like `config_dir` does not.
    return re.compile(
        r"(?<![\w-])" + r"[\s-]+".join(words) + r"(?:s|es|d|ed|ing)?(?![\w-])",
        re.IGNORECASE,
    )


def test_context_dependent_avoid_terms_are_still_avoided() -> None:
    stale = sorted(_CONTEXT_DEPENDENT_AVOID_TERMS.keys() - _glossary_avoid_terms())
    assert stale == [], (
        f"_CONTEXT_DEPENDENT_AVOID_TERMS lists terms docs/CONTEXT.md no longer "
        f"avoids; delete them: {stale}"
    )


def test_glossary_avoid_terms_are_not_used() -> None:
    terms = _glossary_avoid_terms()
    assert terms, "found no `_Avoid_:` terms in docs/CONTEXT.md"
    malformed = [
        term
        for term in terms
        if not re.fullmatch(r"\w[\w' -]*", term) or len(term.split()) > 4
    ]
    assert malformed == [], (
        "these `_Avoid_:` terms did not parse as a short phrase; keep each "
        f"`_Avoid_:` line a comma-separated list of terms: {malformed}"
    )
    banned = {
        term: _avoid_term_pattern(term)
        for term in terms
        if term not in _CONTEXT_DEPENDENT_AVOID_TERMS
    }

    pieces = [
        (rel, 1, (ROOT / rel).read_text(encoding="utf-8"))
        for rel in _repo_markdown_files()
        if not any(
            fnmatch.fnmatchcase(rel.as_posix(), p) for p in _AVOID_TERM_EXEMPT_DOCS
        )
    ]
    for path in sorted((ROOT / "caliper").rglob("*.py")):
        rel = path.relative_to(ROOT)
        pieces += [(rel, line, text) for line, text in _python_prose(path)]

    misuses: list[str] = []
    historical: set[tuple[str, str]] = set()
    for rel, first_line, text in pieces:
        for term, pattern in banned.items():
            for match in pattern.finditer(text):
                if term in _AVOID_TERM_HISTORICAL_USES.get(rel.as_posix(), ()):
                    historical.add((rel.as_posix(), term))
                    continue
                line_no = first_line + text.count("\n", 0, match.start())
                entries = ", ".join(terms[term])
                misuses.append(
                    f"{rel.as_posix()}:{line_no}: {match.group(0)!r} "
                    f"(docs/CONTEXT.md → {entries})"
                )

    assert misuses == [], (
        "these spell a term docs/CONTEXT.md says to avoid. Use the entry's own "
        "term instead; if the word is right in this sense, add it to "
        "_CONTEXT_DEPENDENT_AVOID_TERMS with the reason:\n" + "\n".join(misuses)
    )
    unused = sorted(
        (rel, term)
        for rel, uses in _AVOID_TERM_HISTORICAL_USES.items()
        for term in uses
        if (rel, term) not in historical
    )
    assert unused == [], (
        f"_AVOID_TERM_HISTORICAL_USES allows uses that no longer occur; delete them: "
        f"{unused}"
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


def test_ty_version_pin_in_lockstep() -> None:
    pyproject = _pyproject()
    dev_deps: list[str] = pyproject["project"]["optional-dependencies"]["dev"]
    pyproject_pins = [
        m.group(1)
        for dep in dev_deps
        if (m := re.fullmatch(r"ty==(\d+\.\d+\.\d+)", dep.strip()))
    ]
    assert len(pyproject_pins) == 1, (
        f"expected exactly one ty==X.Y.Z pin in pyproject.toml dev deps: {dev_deps}"
    )

    ty_include = pyproject.get("tool", {}).get("ty", {}).get("src", {}).get("include")
    assert ty_include == ["caliper", "docs", "skills"], (
        f"expected [tool.ty.src].include = ['caliper', 'docs', 'skills'] in pyproject.toml, got {ty_include}"
    )

    lint_workflow_raw = (ROOT / ".github/workflows/lint.yml").read_text(
        encoding="utf-8"
    )
    lint_workflow = yaml.safe_load(lint_workflow_raw)
    lint_steps = lint_workflow.get("jobs", {}).get("lint", {}).get("steps", [])
    ty_steps = [
        step.get("run", "").strip()
        for step in lint_steps
        if "ty check" in step.get("run", "")
    ]
    assert ty_steps, (
        f"expected a step running 'ty check' in .github/workflows/lint.yml lint job, found: {lint_steps}"
    )

    pins: dict[str, list[str]] = {
        "pyproject.toml": pyproject_pins,
    }
    for rel_path in (".github/CONTRIBUTING.md", "AGENTS.md"):
        found = sorted(set(_TY_TEXT_PIN.findall((ROOT / rel_path).read_text("utf-8"))))
        assert found, f"no ty==X.Y.Z or ty@X.Y.Z pin found in {rel_path}"
        pins[rel_path] = found

    all_versions = {version for versions in pins.values() for version in versions}
    assert len(all_versions) == 1, f"keep ty version pins in lockstep: {pins}"


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


# Where a command written in prose or a shell line ends: a closing backtick, a
# pipe, a redirect (not the `>` closing a `<spec>` placeholder), or the next
# command.
_COMMAND_END = re.compile(r"`|\||(?<![\w-])>|;|&&|$", re.MULTILINE)


def _command_spellings(words: list[str]) -> tuple[str, set[str]] | None:
    """The command `caliper <words…>` names, and every flag spelling it takes."""
    command = get_command(app)
    path: list[str] = []
    for word in words:
        subcommands = getattr(command, "commands", {})
        if word not in subcommands:
            break
        command = subcommands[word]
        path.append(word)
    if not path:
        return None
    spellings = {"--help"}
    for param in command.params:
        spellings |= set(param.opts) | set(param.secondary_opts)
    return " ".join(path), spellings


def test_skill_docs_use_only_flags_their_commands_have() -> None:
    stale: list[str] = []
    checked = 0
    for doc in sorted(ROOT.glob("skills/*/*.md")):
        text = doc.read_text(encoding="utf-8")
        for match in re.finditer(r"\bcaliper((?:[ \t]+[a-z][\w-]*)+)", text):
            named = _command_spellings(match.group(1).split())
            if named is None:
                continue
            command, spellings = named
            rest = text[match.end() :]
            end = _COMMAND_END.search(rest)
            line = rest[: end.start() if end else len(rest)]
            checked += 1
            line_no = text.count("\n", 0, match.start()) + 1
            stale += [
                f"{doc.relative_to(ROOT).as_posix()}:{line_no}: caliper {command} {flag}"
                for flag in re.findall(r"(?<![\w-])--[a-z][\w-]*", line)
                if flag not in spellings
            ]
    assert checked, "found no `caliper <command>` in skills/*/*.md"
    assert stale == [], (
        "these skill docs pass a flag their caliper command does not take; "
        "fix the doc, or the command's name for it:\n" + "\n".join(stale)
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


def _github_anchor_slug(heading: str) -> str:
    # GitHub drops punctuation and turns each space into its own hyphen, so
    # "Usable / unusable attempt" -> "usable--unusable-attempt".
    s = re.sub(r"<[^>]+>", "", heading.strip()).lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return s.replace(" ", "-")


def _unfenced_lines(text: str) -> typing.Iterator[tuple[int, str]]:
    """Yield (lineno, line) for every line outside ``` / ~~~ code fences."""
    fence: str | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^(```+|~~~+)", line.strip())
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
                continue
        if fence is None:
            yield lineno, line


def _prose_only(text: str) -> str:
    """Blank out code fences and inline code, keeping every line in place."""
    lines = text.splitlines()
    for lineno, line in _unfenced_lines(text):
        lines[lineno - 1] = "\x00" + re.sub(r"(`+)(.*?)\1", "", line)
    return "\n".join(line[1:] if line.startswith("\x00") else "" for line in lines)


def _extract_heading_anchors(text: str) -> set[str]:
    counts: Counter[str] = Counter()
    anchors: set[str] = set()
    for _, line in _unfenced_lines(text):
        hm = re.match(r"^\s{0,3}#{1,6}\s+(.*?)(?:\s+#+)?$", line)
        if hm:
            base_slug = _github_anchor_slug(hm.group(1))
            count = counts[base_slug]
            counts[base_slug] += 1
            anchors.add(base_slug if count == 0 else f"{base_slug}-{count}")
    return anchors


def test_markdown_relative_links_and_anchors_resolve() -> None:
    files = _repo_markdown_files()
    assert files, "found no markdown files in repository"

    texts = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in files}
    file_anchors = {rel: _extract_heading_anchors(text) for rel, text in texts.items()}

    # Link text may wrap across lines; the target never does.
    link_pattern = re.compile(r"!?\[([^\]]*)\]\(([^)\n]+)\)")
    failures: list[str] = []

    for rel, text in texts.items():
        source_path = ROOT / rel
        prose = _prose_only(text)
        for lm in link_pattern.finditer(prose):
            lineno = prose.count("\n", 0, lm.start()) + 1
            raw_target = lm.group(2).strip()
            target = raw_target.split()[0] if raw_target else ""
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue

            path_part, _, fragment = target.partition("#")
            if not path_part:
                target_rel = rel
                resolved_path = source_path
            else:
                resolved_path = (source_path.parent / path_part).resolve()
                try:
                    target_rel = resolved_path.relative_to(ROOT)
                except ValueError:
                    failures.append(
                        f"{rel}:{lineno} -> ({raw_target}) resolves outside ROOT: {resolved_path}"
                    )
                    continue

            if not resolved_path.exists():
                failures.append(
                    f"{rel}:{lineno} -> ({raw_target}) does not exist: {resolved_path}"
                )
                continue

            if fragment and resolved_path.suffix == ".md":
                if target_rel not in file_anchors:
                    file_anchors[target_rel] = _extract_heading_anchors(
                        resolved_path.read_text(encoding="utf-8")
                    )
                anchors = file_anchors[target_rel]
                if fragment not in anchors:
                    failures.append(
                        f"{rel}:{lineno} -> ({raw_target}) anchor #{fragment} not found in {target_rel}. "
                        f"Available anchors: {sorted(anchors)}"
                    )

    assert not failures, (
        f"broken relative markdown links or anchors ({len(failures)}):\n  "
        + "\n  ".join(failures)
    )


def _workflow_check_contexts(jobs: dict[str, typing.Any]) -> list[str]:
    """The status-check names GitHub reports for each job (one per matrix value)."""
    contexts: list[str] = []
    for job_id, job in jobs.items():
        name = job.get("name", job_id)
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        if not matrix:
            contexts.append(name)
            continue
        for key, values in matrix.items():
            placeholder = f"${{{{ matrix.{key} }}}}"
            for value in values:
                if placeholder in name:
                    contexts.append(name.replace(placeholder, str(value)))
                else:
                    contexts.append(f"{name} ({value})")
    return contexts


def test_contributing_ci_section_matches_workflow() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    )
    jobs: dict[str, typing.Any] = workflow.get("jobs", {})
    assert jobs, ".github/workflows/lint.yml has no jobs"
    contexts = set(_workflow_check_contexts(jobs))

    ci_section = _markdown_section(".github/CONTRIBUTING.md", "## CI")
    table_rows = re.findall(r"^\| `([^`]+)` \|", ci_section, re.MULTILINE)
    assert table_rows, "found no job table in .github/CONTRIBUTING.md ## CI"

    undocumented = sorted(
        job_id
        for job_id in jobs
        if not any(row == job_id or row.startswith(f"{job_id} (") for row in table_rows)
    )
    assert not undocumented, (
        f"lint.yml jobs missing from the .github/CONTRIBUTING.md ## CI table: {undocumented}"
    )
    stale = sorted(set(table_rows) - set(jobs) - contexts)
    assert not stale, (
        f".github/CONTRIBUTING.md ## CI table lists jobs lint.yml doesn't run: {stale}"
    )

    json_match = re.search(r"<<'JSON'\s*\n(.*?)\nJSON", ci_section, re.DOTALL)
    assert json_match is not None, (
        "found no <<'JSON' branch-protection snippet in .github/CONTRIBUTING.md ## CI"
    )
    payload = json.loads(json_match.group(1))
    required = payload.get("required_status_checks", {}).get("contexts", [])
    unknown = sorted(set(required) - contexts)

    assert required, "the branch-protection snippet in ## CI requires no status checks"
    assert not unknown, (
        ".github/CONTRIBUTING.md's branch-protection snippet requires status checks "
        f"that .github/workflows/lint.yml never reports: {unknown}"
    )


FEATURE_MAP = "docs/feature-map.md"
_PROVING_TEST = re.compile(r"`(tests/\w+\.py)::(\w+(?:::\w+)?)`")


def _feature_map_rows() -> list[list[str]]:
    text = (ROOT / FEATURE_MAP).read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| ")]
    cells = [[cell.strip() for cell in row.strip("|").split(" | ")] for row in rows]
    return [row for row in cells if row[0] != "Feature" and row[0].strip("-: ")]


@functools.cache
def _collected_tests(rel_path: str) -> set[str]:
    """What pytest collects from a test file: its test functions and test classes."""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names.add(node.name)
            names |= {
                f"{node.name}::{f.name}"
                for f in node.body
                if isinstance(f, ast.FunctionDef) and f.name.startswith("test_")
            }
        elif isinstance(node, ast.Assign):
            names |= {
                t.id
                for t in node.targets
                if isinstance(t, ast.Name) and t.id.startswith("Test")
            }
    return names


def test_feature_map_has_a_row_for_every_command_and_backend() -> None:
    features = "\n".join(row[0] for row in _feature_map_rows())
    mapped = set(re.findall(r"`caliper ([a-z][\w-]*)", features))
    unmapped = sorted(set(get_command(app).commands) - mapped)
    unmapped += [b for b in sorted(VALID_BACKENDS) if f"`{b}`" not in features]
    assert unmapped == [], f"add a {FEATURE_MAP} row for: {unmapped}"


def test_every_feature_map_row_names_a_test_that_exists() -> None:
    rows = _feature_map_rows()
    assert rows, f"{FEATURE_MAP} has no feature rows"
    unproven = [row[0] for row in rows if not _PROVING_TEST.search(row[-1])]
    assert unproven == [], f"name a proving test for these {FEATURE_MAP} rows"
    missing = [
        f"{path}::{name}"
        for row in rows
        for path, name in _PROVING_TEST.findall(row[-1])
        if not (ROOT / path).is_file() or name not in _collected_tests(path)
    ]
    assert missing == [], (
        f"{FEATURE_MAP} names tests that no longer exist; point each row at the "
        f"test that now proves it: {missing}"
    )

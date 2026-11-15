"""Laws of what caliper reads from a user: engine targets, MCP tool names, spec
YAML and git skill paths, over generated inputs.

See docs/adr/0045 (targets), docs/adr/0043 (MCP names) and docs/adr/0016 (git
sources).
"""

from __future__ import annotations

import contextlib
import posixpath
from pathlib import PureWindowsPath

import pytest
import yaml
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from caliper.backends import (
    EFFORT_WORDS,
    VALID_BACKENDS,
    mcp_server_of,
    mcp_tool_name,
    mcp_tool_separator,
    normalize_backend,
)
from caliper.schema.spec import GitSkillSource, _load_yaml, parse_target

backends = st.sampled_from(sorted(VALID_BACKENDS))
efforts = st.sampled_from(sorted(EFFORT_WORDS))
models = st.from_regex(r"[a-z0-9][a-z0-9.\-/]{0,15}", fullmatch=True)
# Words joined by single separators, as server names are written in a spec.
servers_with_underscores = st.from_regex(
    r"[a-z0-9]+([_-][a-z0-9]+){0,2}", fullmatch=True
)
servers_without_underscores = st.from_regex(
    r"[a-z0-9]+(-[a-z0-9]+){0,2}", fullmatch=True
)
tools = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)


@given(backends, models, st.none() | efforts)
def test_a_target_parses_back_into_its_parts(backend, model, effort) -> None:
    target = f"{backend}:{model}" + (f"@{effort}" if effort else "")
    assert parse_target(target) == (backend, model, effort)


@given(backends, st.none() | efforts)
def test_a_bare_backend_names_no_model(backend, effort) -> None:
    target = backend + (f"@{effort}" if effort else "")
    assert parse_target(target) == (backend, None, effort)


@given(
    models.filter(lambda m: normalize_backend(m) not in VALID_BACKENDS),
    st.none() | efforts,
)
def test_a_bare_model_names_no_backend(model, effort) -> None:
    target = model + (f"@{effort}" if effort else "")
    assert parse_target(target) == (None, model, effort)


@given(models, st.from_regex(r"[0-9]{8}", fullmatch=True))
def test_a_dated_model_keeps_its_at(model, date) -> None:
    # A Vertex-style `claude-sonnet-4@20250514` is a model, not an effort.
    assume(normalize_backend(model) not in VALID_BACKENDS)
    assert parse_target(f"{model}@{date}") == (None, f"{model}@{date}", None)


@given(st.text())
def test_any_target_parses_without_raising(value) -> None:
    backend, model, effort = parse_target(value)
    assert backend is None or isinstance(backend, str)
    assert model is None or isinstance(model, str)
    assert effort is None or isinstance(effort, str)


@given(
    backends.filter(lambda b: mcp_tool_separator(b) == "__"),
    st.lists(servers_with_underscores, min_size=1, max_size=4, unique=True),
    st.data(),
    tools,
)
def test_a_doubled_separator_round_trips_any_server(backend, servers, data, tool):
    # `mail` and `mail_archive` can both be declared: the doubled separator
    # still says where the server name ends.
    server = data.draw(st.sampled_from(servers))
    assert mcp_server_of(mcp_tool_name(backend, server, tool), servers, backend) == (
        server
    )


@given(
    backends,
    st.lists(servers_without_underscores, min_size=1, max_size=4, unique=True),
    st.data(),
    tools,
)
def test_a_server_without_underscores_round_trips_on_every_backend(
    backend, servers, data, tool
) -> None:
    server = data.draw(st.sampled_from(servers))
    assert mcp_server_of(mcp_tool_name(backend, server, tool), servers, backend) == (
        server
    )


def test_a_single_separator_reads_an_underscored_tool_as_the_longer_server() -> None:
    # The known limit of hermes' and antigravity's `mcp_<server>_<tool>`: a call
    # to `archive_read` on `mail` is spelled exactly like `read` on
    # `mail_archive`, and the longer declared server wins (docs/adr/0043).
    for backend in ("hermes", "antigravity"):
        name = mcp_tool_name(backend, "mail", "archive_read")
        assert name == mcp_tool_name(backend, "mail_archive", "read")
        assert mcp_server_of(name, ["mail", "mail_archive"], backend) == "mail_archive"


@given(
    backends,
    st.lists(servers_with_underscores, min_size=1, max_size=4, unique=True),
    st.data(),
    tools,
)
def test_the_server_found_always_prefixes_the_name(backend, servers, data, tool):
    # Under a single `_`, `mcp_mail_archive_read` is ambiguous: the longest
    # declared server wins, and it is always one whose prefix really matches.
    name = mcp_tool_name(backend, data.draw(st.sampled_from(servers)), tool)
    found = mcp_server_of(name, servers, backend)
    assert found in servers
    assert name.startswith(mcp_tool_name(backend, found, ""))
    assert not any(
        len(s) > len(found) and name.startswith(mcp_tool_name(backend, s, ""))
        for s in servers
    )


keys = st.from_regex(r"[a-z]{1,6}", fullmatch=True)


@given(st.lists(keys, min_size=1, max_size=6, unique=True), st.data(), st.booleans())
def test_a_repeated_yaml_key_is_refused(names, data, nested) -> None:
    repeated = data.draw(st.sampled_from(names))
    position = data.draw(st.integers(min_value=0, max_value=len(names)))
    lines = [f"{name}: {i}" for i, name in enumerate(names)]
    lines.insert(position, f"{repeated}: again")
    if nested:
        lines = ["outer:", *(f"  {line}" for line in lines)]
    with pytest.raises(yaml.YAMLError, match=f"duplicate key '{repeated}'"):
        _load_yaml("\n".join(lines))


@given(st.lists(keys, min_size=1, max_size=6, unique=True), st.data())
def test_a_repeated_key_in_a_flow_mapping_inside_a_list_is_refused(names, data):
    repeated = data.draw(st.sampled_from(names))
    pairs = [f"{name}: {i}" for i, name in enumerate(names)] + [f"{repeated}: 0"]
    with pytest.raises(yaml.YAMLError, match=f"duplicate key '{repeated}'"):
        _load_yaml(f"- {{{', '.join(pairs)}}}")


@given(st.text())
def test_any_text_loads_or_raises_a_yaml_error(text) -> None:
    with contextlib.suppress(yaml.YAMLError):
        _load_yaml(text)


@given(st.lists(keys, max_size=6, unique=True))
def test_yaml_without_repeats_loads_as_safe_load_does(names) -> None:
    text = "\n".join(f"{name}: {i}" for i, name in enumerate(names))
    assert _load_yaml(text) == yaml.safe_load(text)


path_parts = st.sampled_from(
    ["skills", "a", ".", "..", "", "C:", "c:", "\\", "..\\..", "~", "SKILL.md"]
)


@given(st.lists(path_parts, max_size=5), st.booleans())
def test_an_accepted_skill_path_stays_inside_the_checkout(parts, rooted) -> None:
    value = ("/" if rooted else "") + "/".join([*parts, "SKILL.md"])
    escapes = rooted or "\\" in value or ".." in value.split("/")
    escapes = escapes or bool(PureWindowsPath(value).drive)
    try:
        GitSkillSource(repo="owner/name", path=value)
    except ValidationError:
        return
    assert not escapes, f"accepted {value!r}"
    # Accepted, so it must resolve inside the checkout however it is split.
    posix = posixpath.normpath(posixpath.join("/checkout", value))
    assert posix.startswith("/checkout/")
    windows = PureWindowsPath("C:/checkout") / value
    assert ".." not in windows.parts
    assert windows.drive == "C:"
    assert str(windows).startswith("C:\\checkout\\")

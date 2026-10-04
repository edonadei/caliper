"""Reading a skill's files for risky patterns, without running anything.

The static half of a trust report (docs/CONTEXT.md → Trust report). It reads
exactly the files an install would copy and flags what a security reviewer asks
about first: where the skill sends data, which secrets it reaches for, whether
it downloads and runs code, hides instructions from the user, or persists on the
machine.

It is a heuristic, and says so. A finding is a place to look, not proof: a
setup guide may well say ``curl … | sh``. Proof of behaviour is the dynamic
half, which runs the skill contained and watches what it does. Most published
skill vulnerabilities are visible statically, which is why the two travel in
one report rather than either standing in for the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from caliper.sandbox import SpecSandbox
from caliper.skills import installs

Severity = Literal["high", "warn", "info"]

#: Matches kept per rule per file: enough to find them, not a wall of them.
_PER_FILE = 5
_EXCERPT = 160
_MAX_FILE_BYTES = 5 * 1024 * 1024

_SCRIPT_SUFFIXES = {
    ".sh",
    ".bash",
    ".zsh",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".rb",
    ".pl",
    ".php",
    ".ps1",
    ".bat",
    ".cmd",
}
_PROSE_SUFFIXES = {".md", ".markdown", ".txt", ".mdx", ".rst"}


class StaticFinding(BaseModel):
    rule: str
    severity: Severity
    file: str
    line: int | None = None
    excerpt: str = ""
    message: str


@dataclass(frozen=True)
class _Rule:
    id: str
    severity: Severity
    pattern: re.Pattern[str]
    message: str
    # Which files it reads: every text file, scripts only, or prose only.
    scope: Literal["any", "script", "prose"] = "any"


_INVISIBLE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\U000e0000-\U000e007f]"
)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


RULES: tuple[_Rule, ...] = (
    _Rule(
        "pipe-to-shell",
        "high",
        _rx(r"\b(curl|wget|iwr|invoke-webrequest)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b"),
        "downloads code and runs it unseen",
    ),
    _Rule(
        "credential-path",
        "high",
        _rx(
            r"\.ssh[/\\](id_|authorized_keys)|\.aws[/\\]credentials|(?<![\w.])\.netrc\b"
            r"|\.config[/\\]gh[/\\]|\.docker[/\\]config\.json|\.kube[/\\]config"
            r"|\.git-credentials|\.npmrc\b|\.pypirc\b|find-generic-password"
            r"|\bkeychain\b|\.codex[/\\]auth\.json|\.claude[/\\]\.credentials"
        ),
        "names a place where credentials live",
    ),
    _Rule(
        "exfil-endpoint",
        "high",
        _rx(
            r"webhook\.site|requestbin|pipedream\.net|ngrok(-free)?\.(io|app)"
            r"|burpcollaborator|interact\.sh|\boast\.(fun|live|site|me|pro)"
            r"|discord(app)?\.com/api/webhooks|hooks\.slack\.com|transfer\.sh"
            r"|paste(bin|\.ee)\b"
        ),
        "names a service commonly used to collect stolen data",
    ),
    _Rule(
        "instruction-override",
        "high",
        _rx(
            r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+instructions"
            r"|disregard\s+(the\s+|your\s+|all\s+)?(previous|prior|system|above)"
            r"|(do\s+not|don'?t|never)\s+(tell|inform|mention|reveal|show)\b[^.\n]{0,40}\buser"
            r"|without\s+(asking|telling|informing|notifying)\s+the\s+user"
            r"|(hide|conceal)\s+(this|it)\s+from\s+the\s+user"
            r"|you\s+are\s+now\s+in\s+developer\s+mode"
        ),
        "tells the agent to override its instructions or keep the user in the dark",
        scope="prose",
    ),
    _Rule(
        "hidden-unicode",
        "high",
        _INVISIBLE,
        "contains invisible or text-reordering characters an agent reads and a reviewer does not see",
    ),
    _Rule(
        "obfuscation",
        "warn",
        _rx(
            r"base64\s+(-d|--decode|-D)\b|b64decode|\batob\s*\(|frombase64string"
            r"|\beval\s*\(|\bexec\s*\(|fromcharcode|(\\x[0-9a-f]{2}){8,}"
            r"|[A-Za-z0-9+/]{200,}={0,2}"
        ),
        "decodes or evaluates code at run time, which hides what it does",
    ),
    _Rule(
        "secret-env",
        "warn",
        _rx(
            r"\bprintenv\b|os\.environ\b|process\.env\b|\benv\s*\|"
            r"|\$\{?[A-Z0-9_]*(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY)[A-Z0-9_]*\b"
        ),
        "reads environment variables, where API keys and tokens live",
        scope="script",
    ),
    _Rule(
        "network",
        "warn",
        _rx(
            r"\b(curl|wget|nc|ncat|netcat|scp|rsync|ftp)\s"
            r"|invoke-webrequest|invoke-restmethod|requests\.(get|post|put)\b"
            r"|urllib|http\.client|\bfetch\s*\(|axios|socket\.(socket|create_connection)"
        ),
        "makes network requests",
        scope="script",
    ),
    _Rule(
        "persistence",
        "warn",
        _rx(
            r"\bcrontab\b|\.(bashrc|zshrc|bash_profile|profile)\b|launchctl"
            r"|LaunchAgents|systemctl\s+(--user\s+)?enable|authorized_keys"
            r"|git\s+config\s+--global"
        ),
        "changes the machine beyond the task: shell startup, schedulers, global config",
    ),
    _Rule(
        "destructive",
        "warn",
        _rx(
            r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|$|/\*)|chmod\s+-R\s+777"
            r"|\bmkfs\b|\bdd\s+if=|git\s+push\s+--force"
        ),
        "can destroy data outside the task",
    ),
    _Rule("privilege", "warn", _rx(r"\bsudo\b|\bdoas\b"), "asks for root"),
)

_URL = re.compile(r"https?://([A-Za-z0-9.-]+)", re.IGNORECASE)
_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_COMMENT_IMPERATIVE = _rx(
    r"\b(agent|assistant|ai|llm|model|claude|codex|instruction|you must|must|always|never)\b"
)
# Hosts every skill links to, which say nothing about where data goes.
_BENIGN_HOSTS = {"example.com", "example.org", "localhost", "127.0.0.1"}


def scan_skill(directory: Path) -> tuple[list[StaticFinding], int]:
    """The findings for the skill at ``directory``, and how many files were read.

    Reads what an install copies: the same exclusions, the same size cap, a
    file link followed like the install follows it. A link that leaves the
    skill directory is itself a finding.
    """
    directory = directory.resolve()
    sandbox = SpecSandbox()
    findings: list[StaticFinding] = []
    hosts: dict[str, tuple[str, int]] = {}
    scanned = 0
    for item in sorted(directory.rglob("*")):
        rel = item.relative_to(directory)
        if item.is_symlink() and not item.resolve().is_relative_to(directory):
            findings.append(
                StaticFinding(
                    rule="link-outside",
                    severity="high",
                    file=rel.as_posix(),
                    excerpt=f"-> {item.resolve()}",
                    message="links to a file outside the skill, which the install copies in",
                )
            )
        if not item.is_file() or not installs(directory, rel, sandbox):
            continue
        try:
            if item.stat().st_size > _MAX_FILE_BYTES:
                continue
            data = item.read_bytes()
        except OSError:
            continue
        scanned += 1
        findings.extend(_scan_file(rel.as_posix(), item, data, hosts))
    for host, (file, line) in sorted(hosts.items()):
        findings.append(
            StaticFinding(
                rule="url",
                severity="info",
                file=file,
                line=line,
                excerpt=host,
                message="links to this host",
            )
        )
    order = {"high": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule, f.file, f.line or 0))
    return findings, scanned


def _scan_file(
    rel: str, path: Path, data: bytes, hosts: dict[str, tuple[str, int]]
) -> list[StaticFinding]:
    found: list[StaticFinding] = []
    suffix = path.suffix.lower()
    is_script = suffix in _SCRIPT_SUFFIXES or (
        data.startswith(b"#!") or _executable(path)
    )
    if b"\0" in data[:8192]:
        return [
            StaticFinding(
                rule="binary",
                severity="warn",
                file=rel,
                message="is a binary file, which this scan cannot read",
            )
        ]
    if is_script:
        found.append(
            StaticFinding(
                rule="script",
                severity="info",
                file=rel,
                message="is a script the agent can run",
            )
        )
    is_prose = suffix in _PROSE_SUFFIXES or not suffix
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    for rule in RULES:
        if rule.scope == "script" and not is_script:
            continue
        if rule.scope == "prose" and not is_prose:
            continue
        hits = 0
        for number, line in enumerate(lines, 1):
            match = rule.pattern.search(line.lstrip("﻿") if number == 1 else line)
            if not match:
                continue
            found.append(
                StaticFinding(
                    rule=rule.id,
                    severity=rule.severity,
                    file=rel,
                    line=number,
                    excerpt=_excerpt(line, match),
                    message=rule.message,
                )
            )
            hits += 1
            if hits >= _PER_FILE:
                break
    if is_prose:
        for match in _COMMENT.finditer(text):
            body = match.group(1)
            if _COMMENT_IMPERATIVE.search(body):
                number = text.count("\n", 0, match.start()) + 1
                found.append(
                    StaticFinding(
                        rule="hidden-comment",
                        severity="warn",
                        file=rel,
                        line=number,
                        excerpt=_excerpt(" ".join(body.split())),
                        message="addresses the agent in a comment a rendered page hides",
                    )
                )
    for number, line in enumerate(lines, 1):
        for match in _URL.finditer(line):
            host = match.group(1).lower().rstrip(".")
            if host not in _BENIGN_HOSTS:
                hosts.setdefault(host, (rel, number))
    return found


def _executable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def _excerpt(line: str, match: re.Match[str] | None = None) -> str:
    line = line.strip()
    if len(line) <= _EXCERPT:
        return _visible(line)
    start = max(0, (match.start() if match else 0) - 40)
    cut = line[start : start + _EXCERPT]
    return _visible(("…" if start else "") + cut + "…")


def _visible(text: str) -> str:
    """Show invisible characters as escapes, so the finding can be seen."""
    return "".join(f"\\u{ord(c):04x}" if _INVISIBLE.match(c) else c for c in text)

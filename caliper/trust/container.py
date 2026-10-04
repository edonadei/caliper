"""Containment: running the agent inside a container runtime (docs/adr/0035).

Caliper does not build a sandbox of its own. With ``--container IMAGE`` it hands
each agent spawn to a Docker-compatible runtime instead, and the container is
what keeps the skill under test away from the host:

- the only host paths it sees are the attempt's own temp dir (its isolated home
  and workdir, mounted at the same path) and the spec's ``extra_path``
  directories, read-only;
- it runs as the invoking user, with every capability dropped and no
  privilege escalation;
- its network is an ``--internal`` one with no route out. The one address it
  can reach is the host's side of that network, where the attempt's
  :class:`~caliper.trust.egress.EgressProxy` listens, so every connection goes
  through the run's egress policy and is logged.

The image supplies the agent CLI (see ``docker/Dockerfile``); its credentials
travel the way they already do, seeded into the isolated home. So the agent's
own login is still within the skill's reach, and a trust report says so.

Steps (``setup:``, ``assert:``, the judge, ``cleanup:``) still run on the host:
they are the spec author's code, not the skill's. A judge-written script is the
exception tracked in #158.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from caliper.harness.base import HarnessConfigurationError

#: Names the container CLI when it is not ``docker`` (``podman``, say).
RUNTIME_ENV = "CALIPER_CONTAINER_RUNTIME"
DEFAULT_RUNTIME = "docker"

_PROBE_TIMEOUT = 60
_PULL_TIMEOUT = 900
_REMOVE_TIMEOUT = 30
# The image's PATH when it declares none: the usual Debian/Alpine one.
_FALLBACK_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class Containment:
    """A run's container setup, shared by every attempt. Built by :func:`contain`."""

    image: str
    runtime: str
    # The run's internal network, and the host's address on it.
    network: str
    gateway: str
    # The image's own PATH, so the agent finds the CLI the image installed.
    path: str

    @property
    def label(self) -> str:
        """How a run records it: ``docker:caliper-agent``."""
        return f"{Path(self.runtime).name}:{self.image}"

    def wrap(
        self,
        cmd: list[str],
        *,
        cwd: str,
        name: str,
        env_file: str,
        mounts: list[tuple[str, bool]],
    ) -> list[str]:
        """The runtime command that runs ``cmd`` contained.

        ``mounts`` are ``(host path, read_only)``, each mounted at the same path
        inside, so every path a harness wrote into a config still resolves. The
        agent's environment travels in ``env_file`` rather than on argv, where
        any local user could read it from the process list.
        """
        args = [
            self.runtime,
            "run",
            "--rm",
            "--interactive",
            "--init",
            "--name",
            name,
            "--network",
            self.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env-file",
            env_file,
            "--workdir",
            cwd,
        ]
        if hasattr(os, "getuid"):
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        for path, read_only in mounts:
            args += ["--volume", f"{path}:{path}" + (":ro" if read_only else "")]
        return [*args, self.image, *cmd]

    def remove(self, name: str) -> None:
        """Stop and remove a container, if it is still there.

        Killing the runtime's client (a timeout, a Ctrl-C) does not stop the
        container it started, so this runs after every spawn.
        """
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [self.runtime, "rm", "--force", name],
                capture_output=True,
                timeout=_REMOVE_TIMEOUT,
            )


def write_env_file(env: dict[str, str]) -> str:
    """Stage an environment for ``--env-file``, owner-only; the caller removes it.

    Outside the attempt's temp dir on purpose: that directory is mounted into
    the container, and this file is not the agent's to read back. A value with
    a newline cannot be expressed in the format and is dropped.
    """
    fd, path = tempfile.mkstemp(prefix="caliper-env-", suffix=".list")
    with os.fdopen(fd, "w") as f:
        for key, value in env.items():
            if "\n" in value or "\r" in value or "=" in key:
                continue
            f.write(f"{key}={value}\n")
    return path


def container_name() -> str:
    return f"caliper-{uuid.uuid4().hex[:16]}"


@contextmanager
def contain(
    image: str, *, cli: str | None, runtime: str | None = None
) -> Iterator[Containment]:
    """Set up containment for one run, or raise ``HarnessConfigurationError``.

    Everything that could fail is checked here, before any paid attempt: the
    runtime answers, the image is present (pulled if not), the agent CLI runs
    inside it, and the egress proxy can listen on the run's network. The
    network is removed on the way out.
    """
    if sys.platform == "win32":
        raise HarnessConfigurationError(
            "--container is not supported on Windows yet: the attempt's paths "
            "cannot be mounted at the same place inside a Linux container."
        )
    runtime = runtime or os.environ.get(RUNTIME_ENV) or DEFAULT_RUNTIME
    found = shutil.which(runtime)
    if found is None:
        raise HarnessConfigurationError(
            f"--container needs a container runtime, and {runtime!r} is not on "
            f"PATH.\n\nInstall Docker (or set {RUNTIME_ENV} to a Docker-compatible "
            "CLI such as podman) and retry."
        )
    runtime = found
    _check(
        [runtime, "version"],
        f"The container runtime {runtime!r} is installed but not answering. "
        "Start it (is the Docker daemon running?) and retry.",
    )
    inspected = _run([runtime, "image", "inspect", image])
    if inspected.returncode != 0:
        _check(
            [runtime, "pull", image],
            f"Could not find or pull the image {image!r}.\n\nBuild the agent image "
            "with `docker build -t caliper-agent docker/` from a caliper checkout, "
            "or name one that has your agent CLI installed.",
            timeout=_PULL_TIMEOUT,
        )
        inspected = _run([runtime, "image", "inspect", image])
    path = _image_path(inspected.stdout)

    if cli:
        _check(
            [runtime, "run", "--rm", "--network", "none", image, cli, "--version"],
            f"The image {image!r} cannot run `{cli} --version`.\n\nInstall the "
            f"agent CLI in the image (docker/Dockerfile installs claude and "
            "codex), or pick the backend it has with --model.",
        )

    network = f"caliper-{uuid.uuid4().hex[:12]}"
    _check(
        [runtime, "network", "create", "--internal", network],
        f"Could not create an internal network with {runtime!r}.",
    )
    try:
        gateway = _gateway(runtime, network)
        _check_listenable(gateway)
        yield Containment(
            image=image, runtime=runtime, network=network, gateway=gateway, path=path
        )
    finally:
        _run([runtime, "network", "rm", network])


def _run(cmd: list[str], timeout: int = _PROBE_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _check(cmd: list[str], message: str, timeout: int = _PROBE_TIMEOUT) -> str:
    result = _run(cmd, timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        suffix = f"\n\nThe runtime said: {detail[0]}" if detail else ""
        raise HarnessConfigurationError(message + suffix)
    return result.stdout


def _image_path(inspect_json: str) -> str:
    try:
        data = json.loads(inspect_json)
        env = data[0]["Config"]["Env"] or []
    except (ValueError, LookupError, TypeError):
        return _FALLBACK_PATH
    for entry in env:
        if isinstance(entry, str) and entry.startswith("PATH="):
            return entry[len("PATH=") :]
    return _FALLBACK_PATH


def _gateway(runtime: str, network: str) -> str:
    out = _check(
        [runtime, "network", "inspect", network],
        f"Could not inspect the network {network!r}.",
    )
    try:
        configs = json.loads(out)[0].get("IPAM", {}).get("Config") or []
    except (ValueError, LookupError, AttributeError):
        configs = []
    for config in configs:
        gateway = (config or {}).get("Gateway")
        if gateway:
            return gateway
    raise HarnessConfigurationError(
        f"The internal network {network!r} has no gateway address, so the "
        "agent would have no way to reach the egress proxy."
    )


def _check_listenable(address: str) -> None:
    """Whether the host can listen on the network's gateway address.

    True on Linux, where the gateway is a host interface. Docker Desktop keeps
    its networks inside a VM, and there the agent could not reach the proxy.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((address, 0))
    except OSError as exc:
        raise HarnessConfigurationError(
            f"The egress proxy cannot listen on {address}, the host's address on "
            f"the container network ({exc}). Containment needs a runtime whose "
            "networks are host interfaces, such as Docker Engine on Linux; Docker "
            "Desktop keeps them inside a VM."
        ) from exc

"""The egress proxy: every connection an attempt's agent asks for, allowed or refused.

A run that watches egress (``sandbox.egress:``, or any contained run) starts
one :class:`EgressProxy` per attempt and points the agent at it through
``HTTPS_PROXY`` and friends. The proxy tunnels ``CONNECT`` and forwards
plain-HTTP requests to the hosts the run's :class:`EgressPolicy` allows, refuses
the rest with a 403, and records both (docs/CONTEXT.md → Egress policy).

What it can see is the host and port of a TLS tunnel, and the whole URL of a
plain-HTTP request. It never terminates TLS, so it reads no HTTPS body.

Uncontained, a process that ignores the proxy variables goes around it, so the
log is advisory. Contained, the agent's network has no other route out, so the
log is complete and the policy holds (docs/adr/0035).

A proxy configured in caliper's own environment (``HTTPS_PROXY``,
``HTTP_PROXY``, honouring ``NO_PROXY``) is used as the upstream, so a run
behind a corporate proxy still reaches the hosts it allows.
"""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import ipaddress
import os
import select
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from caliper.schema.results import EgressEvent

#: Request targets kept per host, as evidence.
_TARGETS_KEPT = 5
_TARGET_LEN = 500
_HEADER_LIMIT = 65536
_CONNECT_TIMEOUT = 15
_RELAY_CHUNK = 65536


@dataclass(frozen=True)
class EgressPolicy:
    """The hosts an attempt's agent may reach.

    A pattern is a host name, matched case-insensitively, where ``*.`` matches
    any subdomain (``*.openai.com`` matches ``api.openai.com``, not
    ``openai.com``). Ports are not part of the policy.
    """

    allow: tuple[str, ...] = ()

    def allows(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(fnmatch.fnmatchcase(host, p.lower()) for p in self.allow)

    def with_hosts(self, *hosts: str) -> EgressPolicy:
        """This policy plus ``hosts``, duplicates dropped, order kept."""
        return EgressPolicy(tuple(dict.fromkeys([*self.allow, *hosts])))


def host_of(url: str) -> str | None:
    """The host a URL names, or ``None`` when it names none."""
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def valid_host_pattern(pattern: str) -> bool:
    """Whether ``pattern`` is a host name, an IP address, or ``*.`` + a host."""
    host = pattern[2:] if pattern.startswith("*.") else pattern
    if not host or any(c in host for c in "/:@ *?[]"):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return all(
        label and len(label) <= 63 and label.replace("-", "").replace("_", "").isalnum()
        for label in host.split(".")
    )


def proxy_env(url: str) -> dict[str, str]:
    """The variables that send an agent's HTTP clients through ``url``.

    Both spellings, because Node, Rust, Python and curl each read a different
    one, and an empty ``NO_PROXY`` so nothing the agent's host excluded slips
    past.
    """
    env = {}
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        env[name] = url
        env[name.lower()] = url
    env["NO_PROXY"] = env["no_proxy"] = ""
    return env


@dataclass(frozen=True)
class _Upstream:
    host: str
    port: int
    auth: str | None

    @classmethod
    def from_url(cls, url: str | None) -> _Upstream | None:
        if not url:
            return None
        if "://" not in url:
            url = "http://" + url
        parts = urlsplit(url)
        if not parts.hostname:
            return None
        auth = None
        if parts.username:
            raw = f"{parts.username}:{parts.password or ''}".encode()
            auth = "Basic " + base64.b64encode(raw).decode()
        return cls(parts.hostname, parts.port or 80, auth)


def _bypasses(host: str, no_proxy: str) -> bool:
    """Whether ``NO_PROXY`` sends ``host`` direct rather than upstream."""
    host = host.lower()
    for entry in (e.strip().lower() for e in no_proxy.split(",")):
        if not entry:
            continue
        if entry == "*":
            return True
        entry = entry.split(":")[0].lstrip("*")
        if host == entry.lstrip(".") or host.endswith(
            entry if entry.startswith(".") else "." + entry
        ):
            return True
    return False


@dataclass
class _Log:
    """What the proxy saw, keyed by host, port and verdict, safe across threads."""

    events: dict[tuple[str, int, bool], EgressEvent] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, host: str, port: int, allowed: bool, target: str) -> None:
        key = (host.lower(), port, allowed)
        with self.lock:
            event = self.events.get(key)
            if event is None:
                self.events[key] = EgressEvent(
                    host=key[0], port=port, allowed=allowed, targets=[]
                )
                event = self.events[key]
            else:
                event.count += 1
            if len(event.targets) < _TARGETS_KEPT and target not in event.targets:
                event.targets.append(target[:_TARGET_LEN])

    def snapshot(self) -> list[EgressEvent]:
        with self.lock:
            return [
                event.model_copy(deep=True)
                for _, event in sorted(self.events.items(), key=lambda kv: kv[0])
            ]


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, proxy: EgressProxy) -> None:
        self.proxy = proxy
        super().__init__(address, _Handler)


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def handle(self) -> None:
        proxy = self.server.proxy
        client: socket.socket = self.request
        proxy._track(client)
        try:
            head, rest = _read_head(client)
            if head is None:
                return
            request_line, _, headers = head.partition(b"\r\n")
            parts = request_line.decode("latin-1").split()
            if len(parts) != 3:
                _reply(client, 400, "Bad Request")
                return
            method, target, version = parts
            if method.upper() == "CONNECT":
                proxy._tunnel(client, target, rest)
            else:
                proxy._forward(client, method, target, version, headers, rest)
        except OSError:
            pass
        finally:
            proxy._untrack(client)


class EgressProxy:
    """A logging, allow-listing forward proxy, for one attempt.

    Use as a context manager: entering starts it on an ephemeral port of
    ``bind_host``; leaving stops it and closes any tunnel still open. Read
    :attr:`events` once the agent has exited.
    """

    def __init__(
        self,
        policy: EgressPolicy,
        *,
        bind_host: str = "127.0.0.1",
        upstream_env: dict[str, str] | None = None,
    ) -> None:
        self.policy = policy
        self.bind_host = bind_host
        env = os.environ if upstream_env is None else upstream_env
        self._https_upstream = _Upstream.from_url(
            env.get("HTTPS_PROXY") or env.get("https_proxy")
        )
        self._http_upstream = _Upstream.from_url(
            env.get("HTTP_PROXY") or env.get("http_proxy")
        )
        self._no_proxy = env.get("NO_PROXY") or env.get("no_proxy") or ""
        self._log = _Log()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._open: set[socket.socket] = set()
        self._open_lock = threading.Lock()

    def __enter__(self) -> EgressProxy:
        self._server = _Server((self.bind_host, 0), self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="caliper-egress-proxy",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        with self._open_lock:
            for sock in list(self._open):
                with contextlib.suppress(OSError):
                    sock.close()
            self._open.clear()

    @property
    def url(self) -> str:
        """Where the agent should send its traffic."""
        if self._server is None:
            raise RuntimeError("EgressProxy used outside its `with` block")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def events(self) -> list[EgressEvent]:
        return self._log.snapshot()

    # --- connections -----------------------------------------------------

    def _track(self, sock: socket.socket) -> None:
        with self._open_lock:
            self._open.add(sock)

    def _untrack(self, sock: socket.socket) -> None:
        with self._open_lock:
            self._open.discard(sock)

    def _upstream_for(self, host: str, *, tls: bool) -> _Upstream | None:
        upstream = self._https_upstream if tls else self._http_upstream
        if upstream is None or _bypasses(host, self._no_proxy):
            return None
        return upstream

    def _open_to(self, host: str, port: int, *, tls: bool) -> socket.socket:
        """A socket to ``host:port``: direct, or tunnelled through the upstream."""
        upstream = self._upstream_for(host, tls=tls)
        if upstream is None:
            return socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        sock = socket.create_connection(
            (upstream.host, upstream.port), timeout=_CONNECT_TIMEOUT
        )
        request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        if upstream.auth:
            request += f"Proxy-Authorization: {upstream.auth}\r\n"
        sock.sendall((request + "\r\n").encode())
        head, _ = _read_head(sock)
        status = head.split(b" ", 2)[1:2] if head else []
        if not status or status[0] != b"200":
            sock.close()
            raise OSError(f"upstream proxy refused CONNECT {host}:{port}")
        return sock

    def _tunnel(self, client: socket.socket, target: str, rest: bytes) -> None:
        host, port = _split_host_port(target, 443)
        if host is None:
            _reply(client, 400, "Bad Request")
            return
        allowed = self.policy.allows(host)
        self._log.record(host, port, allowed, f"{host}:{port}")
        if not allowed:
            _reply(client, 403, "Forbidden by caliper egress policy")
            return
        try:
            remote = self._open_to(host, port, tls=True)
        except OSError:
            _reply(client, 502, "Bad Gateway")
            return
        self._track(remote)
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                remote.sendall(rest)
            _relay(client, remote)
        finally:
            self._untrack(remote)
            remote.close()

    def _forward(
        self,
        client: socket.socket,
        method: str,
        target: str,
        version: str,
        headers: bytes,
        rest: bytes,
    ) -> None:
        parts = urlsplit(target)
        if parts.scheme.lower() != "http" or not parts.hostname:
            _reply(client, 400, "Bad Request")
            return
        host, port = parts.hostname, parts.port or 80
        allowed = self.policy.allows(host)
        self._log.record(host, port, allowed, target)
        if not allowed:
            _reply(client, 403, "Forbidden by caliper egress policy")
            return
        upstream = self._upstream_for(host, tls=False)
        try:
            if upstream is None:
                remote = socket.create_connection(
                    (host, port), timeout=_CONNECT_TIMEOUT
                )
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                line = f"{method} {path} {version}"
            else:
                remote = socket.create_connection(
                    (upstream.host, upstream.port), timeout=_CONNECT_TIMEOUT
                )
                line = f"{method} {target} {version}"
                if upstream.auth:
                    headers += f"\r\nProxy-Authorization: {upstream.auth}".encode()
        except OSError:
            _reply(client, 502, "Bad Gateway")
            return
        self._track(remote)
        try:
            kept = [
                h
                for h in headers.split(b"\r\n")
                if h and not h.lower().startswith(b"proxy-connection:")
            ]
            remote.sendall(
                line.encode("latin-1") + b"\r\n" + b"\r\n".join(kept) + b"\r\n\r\n"
            )
            if rest:
                remote.sendall(rest)
            _relay(client, remote)
        finally:
            self._untrack(remote)
            remote.close()


def _split_host_port(target: str, default: int) -> tuple[str | None, int]:
    if target.startswith("["):
        host, _, tail = target[1:].partition("]")
        port = tail.lstrip(":")
    else:
        host, _, port = target.rpartition(":") if ":" in target else (target, "", "")
    try:
        return (host or None), int(port) if port else default
    except ValueError:
        return None, default


def _read_head(sock: socket.socket) -> tuple[bytes | None, bytes]:
    """Read up to the end of an HTTP head; the head and any bytes after it."""
    data = b""
    sock.settimeout(_CONNECT_TIMEOUT)
    while b"\r\n\r\n" not in data:
        if len(data) > _HEADER_LIMIT:
            return None, b""
        chunk = sock.recv(8192)
        if not chunk:
            return None, b""
        data += chunk
    sock.settimeout(None)
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _reply(sock: socket.socket, status: int, reason: str) -> None:
    body = f"{reason}\n".encode()
    with contextlib.suppress(OSError):
        sock.sendall(
            f"HTTP/1.1 {status} {reason}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )


def _relay(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes both ways until either side closes."""
    a.settimeout(None)
    b.settimeout(None)
    sockets = [a, b]
    while True:
        try:
            readable, _, broken = select.select(sockets, [], sockets, 60)
        except (OSError, ValueError):
            return
        if broken:
            return
        if not readable:
            continue
        for sock in readable:
            other = b if sock is a else a
            try:
                chunk = sock.recv(_RELAY_CHUNK)
            except OSError:
                return
            if not chunk:
                return
            try:
                other.sendall(chunk)
            except OSError:
                return

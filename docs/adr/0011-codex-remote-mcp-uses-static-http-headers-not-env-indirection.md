# Codex remote MCP uses static `http_headers`, not Codex's env-var indirection

Codex's `config.toml` offers three ways to authenticate a `streamable_http` MCP
server: a static `http_headers` map (literal header values), `env_http_headers`
(header name → env-var name, read from the child's environment at connect time),
and `bearer_token_env_var` (an env-var name holding a bearer token). Codex's own
docs steer toward the env-var forms. We deliberately use `http_headers` with
**boundary-resolved literal values** instead.

The reason is [ADR
0009](0009-mcp-secrets-interpolated-at-the-harness-boundary.md): a declared
server's `${VAR}` secrets are resolved once, at the harness boundary, from the
developer's real `os.environ`, and the resolved value exists only in the config
file the isolated agent reads. Every other aspect of a Codex attempt runs in a
stripped isolated `HOME`, so the child process does **not** carry the developer's
ambient environment — an `env_http_headers` / `bearer_token_env_var` reference
would look up a var that isn't there and the server would fail opaquely at connect
time. Writing the literal into `http_headers` keeps Codex's remote secret path
identical to how `claude-code` fills `headers` and `hermes` fills its `headers`:
one secret model across all backends, resolved at one boundary, with an unset var
raising `HarnessConfigurationError` up front rather than deep inside the agent.

## Consequences

- The materialized `config.toml` holds resolved secrets, so it is written `0600`
  (as on the other backends).
- Caliper's `mcp:` spec has no way to express Codex's env-var indirection, and
  that is intentional — the spec stays backend-agnostic and the harness owns the
  translation.

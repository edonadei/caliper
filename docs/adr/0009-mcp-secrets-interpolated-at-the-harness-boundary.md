# MCP secrets are host-env vars, interpolated at the harness boundary

An `env:` value in an `mcp:` server may reference a host environment variable
with `${VAR}` (e.g. `env: { API_TOKEN: ${MCP_API_TOKEN} }`) so a real secret
never lives in the committed spec. Interpolation is deliberately narrow and late:
`${VAR}` is the only form (no bare `$VAR`, no `${VAR:-default}`), it is honored
**only inside `env:` values** (never in `command`/`args`), and it is resolved by
the `claude-code` harness at materialization time — reading the developer's real
`os.environ` as it writes the MCP config into the run's isolated HOME. The parsed
`EvalSpec` keeps the literal `${VAR}`; the resolved secret exists only in the
config file the spawned agent reads.

The subtlety worth recording: every other aspect of an attempt runs in a fresh
isolated HOME with the developer's real config stripped, yet secret resolution
deliberately reaches back into the real parent environment. That is the whole
point — the isolation strips *ambient config and state*, not the *declared*
credentials a skill genuinely needs, and pulling them from the host env (rather
than the spec) is what keeps secrets out of version control.

## Considered options

- **Interpolate at spec load.** Rejected: the parsed `EvalSpec` (and anything
  that serializes it) would then hold the resolved secret, and `caliper validate`
  would fail on any machine where the var is unset — validation would require the
  production secret to be present just to check grammar.
- **Pass the config inline via `--mcp-config '<json>'`.** Rejected: the resolved
  secret would land in the process argv, visible to any `ps` on the machine.
  Instead the harness writes the interpolated config to a file in the 0700 run
  tempdir and passes `--mcp-config <path> --strict-mcp-config` (strict so the
  attempt sees *only* the declared servers, matching the stripped-HOME
  invariant); the file is removed by the existing cleanup callback.
- **Substitute empty string for an unset var** (shell default). Rejected: the MCP
  server then starts with a blank credential and fails opaquely deep inside the
  agent. A missing var raises `HarnessConfigurationError` naming the var, the
  server, and the `env` key that referenced it — a config error, surfaced at the
  boundary, not a mysterious skill failure.

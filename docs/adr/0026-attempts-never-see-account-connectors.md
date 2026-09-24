# Attempts never see the account's hosted connectors

> Relaxed by [0028](0028-inherit-mcp-is-an-opt-in-invocation-flag.md): a run can
> opt back in with `--inherit-mcp`. The default below is unchanged.

An attempt sees exactly the MCP servers its spec declares, and none when it
declares no `mcp:` block. That already held for servers in the seeded config
files, but the login caliper seeds to authenticate the CLI also brings the
*account's* hosted connectors: claude.ai connectors on `claude-code` (Gmail,
Drive, Calendar, Docs) and ChatGPT apps and remote plugins on `codex` (over 200
`mcp__codex_apps__*` tools, plus plugin bundles with their own skills). They
don't live in any file caliper rewrites, so stripping config left them in play
(#129). Under `--dangerously-skip-permissions` a skill could drive a real
account without a prompt, and a score depended on which connectors the person
running the eval happened to have.

So each backend turns them off at the invocation:

- `claude-code` always passes `--strict-mcp-config`, with an empty `mcpServers`
  config when the spec has no `mcp:` block. This supersedes the "no block keeps
  the CLI's ambient config" half of
  [0025](0025-ablation-covers-mcp-servers.md#an-empty-declared-set-still-isolates):
  no block, `mcp: {}`, and an all-ablated block now all isolate to zero servers.
- `codex` passes `-c features.apps=false -c features.plugins=false`. A `-c`
  override wins over any `[features]` table in the seeded `config.toml`, so it
  needs no TOML merge. It is a second local exception to
  [0012](0012-cli-harnesses-copy-cli-config-verbatim.md)'s "copy verbatim",
  beside the model strip.

The judge gets the same switches. It reads the attempt's transcript with its own
context, and a judge that can see the account's connectors mistakes them for the
attempt's tools.

## Consequences

- A skill that relies on an account connector can't be evaluated through it.
  Declare the server under `mcp:` instead, so the spec says what it needs.
- The smoke evals carry probe tasks that ask the agent to name its connectors, so
  a CLI update that adds a new source of them fails there. That is also the only
  guard on codex's feature names: codex ignores a `-c features.<name>` it doesn't
  know, so a renamed feature would bring the leak back silently.

# Caliper owns the ChatGPT plan login

A codex attempt used to authenticate with a copy of the user's
`~/.codex/auth.json`, deleted with the attempt's home. Codex rotates its refresh
token on every refresh and refuses a reused one. An attempt that refreshed threw
away the new token and left the user's own file holding a spent one, so their
next refresh failed until they ran `codex login` again. With `--workers 4`,
every attempt that started after the access token expired refreshed with the
same token, and at most one succeeded (#252).

`caliper login chatgpt` makes Caliper its own client in OpenAI's open-source
Sign in with ChatGPT tier. It registers Caliper, signs in through the browser
with PKCE, and stores the login under Caliper's config dir
(`~/.config/caliper/chatgpt.json`), never in `~/.codex`. Only Caliper's own
process refreshes it, one refresh at a time across threads and processes, and
each refresh re-reads the file first. An attempt gets a short-lived access
token in `CALIPER_CHATGPT_ACCESS_TOKEN`, sized to last its timeout, and a model
provider that reads it (`env_key`, `requires_openai_auth = false`). No
`auth.json` is seeded, so nothing in the attempt can refresh or spend the login.
The judge uses the same token and provider.

The provider goes in as `-c` overrides rather than in the `config.toml` Caliper
writes. The judge reads the real home, where Caliper writes no config, and one
spelling covers both. A `-c` override also beats any provider the user's own
config selects.

A signed-in login always wins. Without one, the codex backend copies
`~/.codex/auth.json` as before, with the old hazards. A refresh the server
refuses keeps the login and stops the run with `Not logged in`, pointing at
`caliper login chatgpt`. Clearing it would quietly drop the run back to the
copied `auth.json`.

## Considered options

- **Move the backend to `codex app-server`**, where OpenAI documents the
  `env_key` setup. Rejected: `codex exec` honours the same provider (checked
  against a capture server on codex-cli 0.162.0), and the stream reader,
  planted history and judge already speak `exec`.
- **Keep copying `auth.json`, and write it back or refresh it once before the
  run.** Rejected as the fix: write-back still races between parallel
  attempts, and codex has no refresh-only command. It stays the fallback for
  users who don't sign in through Caliper.
- **Verify the ID token's signature against OpenAI's JWKS.** Rejected: it
  would add a crypto dependency. The token comes straight from the token
  endpoint over TLS, which OIDC Core 3.1.3.7 accepts in place of the signature.
  Caliper still checks its issuer, audience, expiry and nonce.

## Consequences

- An access token lasts an hour. An attempt can't refresh mid-run, so an
  attempt with a longer `--timeout` gets a fresh token and may still see it
  expire.
- Plan usage rejects some Responses features (hosted connectors,
  `tool_search`, `temperature`), so an eval that needs them fails on a plan
  login. `caliper logout chatgpt` goes back to the Codex CLI's own login.
- A fresh CI runner has no browser and keeps no refreshed tokens, so this
  doesn't unblock CI on a ChatGPT plan (#192). API keys remain the CI path.
- A remote machine can take a copied `chatgpt.json`. It keeps its own
  `host-id` file, which isn't copied, and refreshes on its own from then on.

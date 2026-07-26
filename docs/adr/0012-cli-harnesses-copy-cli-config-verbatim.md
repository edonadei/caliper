# CLI harnesses copy the CLI's config verbatim; codex's model-strip is a deliberate exception

The CLI-subprocess harnesses (`claude-code`, `codex`, `pi`) copy the underlying
CLI's own auth/config **verbatim** into the isolated per-attempt `HOME` and let
the CLI resolve its own default model. A spec's `model:` — passed as the CLI's
`--model` flag — overrides when set. `claude-code` copies `.claude.json` /
credentials and mutates nothing (reference behavior); `pi` copies
`~/.pi/agent/{auth.json,settings.json}` verbatim, keeping `settings.json`'s
`defaultProvider`/`defaultModel`. `codex` is the one exception: it strips the
top-level `model =` from the copied `config.toml`
(`_strip_seeded_config` in `caliper/harness/codex.py`) so a spec with no
`model:` falls back to the Codex CLI's built-in default.

The rule is uniform — *copy verbatim, the CLI resolves the default, `--model`
overrides* — and codex's strip is a **local** exception, not a different rule.
It is tempting to "unify" by making `pi` strip its default like `codex`, or
making `codex` keep its default like the others; both naive unifications are
wrong. pi's *built-in* default provider is `google`, but a typical user's
`auth.json` holds only e.g. `anthropic` credentials — so stripping pi's
configured default would make an unspecified-model spec resolve to google and
**fail auth outright**. codex's built-in default is sane, so stripping its
config-pinned model is the right call there. The asymmetry exists precisely
because codex has a usable built-in default that pi lacks.

## Consequences

- Copying config verbatim means an attempt **inherits the machine's configured
  default model** whenever the spec omits `model:` — two people running the same
  spec on differently-configured machines can get different models. This already
  affected `claude-code` (inherits the subscription default); `pi` introduces
  nothing new, but it is a real reproducibility gap.
- **Do not collapse the codex-strip vs. pi/claude-code-keep behaviors** without
  first addressing the google-default auth failure above.

## Superseding

A future **unified harness config** that owns model/provider/credential
resolution across all backends would make "no `model:` in spec" resolve to an
explicit, machine-independent default, remove the per-backend copy/strip
special-casing, and supersede this ADR. Surfaced while adding the `pi` backend
(#9); originally recorded as issue #10 before the `docs/adr/` practice existed.

# Install-and-discover is the only loading discipline

Every skill an eval declares is **installed at the backend's native skills root**
(`<skills_root>/<frontmatter name>/SKILL.md`) and nothing is ever preloaded into
the agent's context. The agent discovers the skill the way a real user's agent
does — from a name and a `description` — and *choosing* it becomes an observable
([[activation]]) rather than something caliper does on its behalf. This deletes
codex's invented `[Skill context]` prompt prepend, pi's `--skill`, hermes'
`--skills` (whose own docs read "**Preload** one or more skills"), moves
claude-code from `.claude/commands/` to `.claude/skills/`, and removes the cwd
copy in `_stage_skill_directory`.

Force-loading, not discovery, was the inconsistent half. Two flag names settle
it: pi's `--no-skills` and hermes' "preload" both exist *because discovery is the
default*, and codex has no force-load flag at all — caliper was inventing a
mechanism the CLI does not offer. Install-and-discover is the one discipline all
four backends actually implement, so it is the only one that means the same
thing on all four.

## Considered options

- **Keep force-loading and add a separate triggering mode.** Rejected — this was
  the original framing of issue #18, and reading the code invalidated it.
  `claude-code` never force-loaded: `_command` wrote `SKILL.md` to
  `.claude/commands/SKILL-vrd-<uid>.md` and passed the prompt unmodified, so the
  skill only ever loaded *because the model chose it*. Every `claude-code` score
  in this repo was already a joint measurement with no way to separate the
  halves, and the skill fired under the mangled filename `SKILL-vrd-13639d6f`
  rather than its frontmatter `name:` — which alone made a separate mode
  unimplementable, since nothing downstream could match a skill to a spec entry.
- **Install *and* preload, measuring activation as a by-product.** Rejected: a
  preloaded skill is already in context, so "did the agent reach for it" has no
  answer. The two are mutually exclusive by construction, not merely awkward
  together.
- **Keep the cwd copy alongside the install.** Rejected: it is a back-door. An
  attempt can reach the skill through the working directory without activating
  it, so a `pass` no longer implies the agent chose the skill — which makes the
  activation number unfalsifiable rather than merely noisy.

## Consequences

- **The execution score becomes a joint measurement of invocation × execution,
  deliberately.** A skill with an excellent body and a poor `description` now
  scores near-zero, and the `activated` column only tells you *why*. This is the
  real cost of the ADR and it is accepted: it is what the skill's users would
  experience. A `--force-load` flag to isolate the body is a named follow-up,
  kept out of v1 so the default measures the shipped artifact.
- **Identity is the frontmatter `name:`, and caliper establishes it rather than
  discovering it.** Because caliper is the installer and installs at
  `<skills_root>/<name>/`, the backend reports back exactly the name the spec
  wrote. This is also what the Agent Skills standard requires (name == parent
  directory).
- **Lone slash-command `.md` files become untestable and are rejected at
  `validate`.** They have no directory, no frontmatter `name:`, and no
  `description:` — nothing to install and nothing for an agent to discover. A
  hard error is the honest response; synthesizing a wrapper would install a
  description-less file and report its guaranteed 0% as a measurement, which is
  the same class of lie as the mangled `SKILL-vrd-<uid>` name this ADR removes.
  Invoking slash commands as a *second*, explicitly-invoked discipline is a
  follow-up with its own ADR — not something to smuggle in beside the first.
- **Cheat-surface exclusions move onto the install path and apply to every
  declared skill.** `_stage_skill_directory` refused to copy `.eval.yaml`,
  `.caliper/`, `.git/`, and `sandbox.forbidden_files`; the install is a new code
  path whose natural implementation is a plain `copytree`, so those exclusions
  have to be carried over deliberately. A neighbour's `.eval.yaml` is as much an
  answer key as the subject's.
- **Progressive disclosure (#19) needs re-verification.** It closed on evidence
  produced by the cwd copy, which this ADR removes. The payoff should survive —
  relative pointers now resolve inside the installed skill directory, as in a
  real install — but it has not been re-measured under the new mechanism.
- **Every pre-existing run is incomparable, and the two halves of the repo are
  wrong in different directions.** `claude-code` runs measured invocation ×
  execution under a mangled name; `codex`/`pi`/`hermes` runs measured execution
  with the skill force-loaded. Post-change runs are neither. `RunMeta` therefore
  carries an explicit era marker and `compare` **refuses** a cross-era diff — see
  [0014](0014-activation-is-a-check-type-not-a-separate-command.md).

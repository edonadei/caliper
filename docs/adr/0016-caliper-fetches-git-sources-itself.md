# Caliper fetches git sources itself rather than delegating to a skills package manager

A `skills:` entry may now be a **git source** — `{repo:, ref:, path:}` — which
caliper materializes with `git clone --depth 1` into a content-addressed cache,
resolving the ref to a concrete commit itself. It does **not** shell out to a
skill package manager to do this.

## Context: the neighbourhood is empty everywhere

Every `.eval.yaml` in this repo declares exactly one entry, a local
`./SKILL.md`. The [[skill neighbourhood]] is a one-member set in every spec that
actually exists, which means every activation precision number here is measured
against *no competition at all* — the one thing the neighbourhood exists to
provide. Declaring a realistic competitor today means cloning someone's repo by
hand and hard-coding an absolute path into a spec that then runs on one machine.

That is the problem a [[skill source|git source]] solves, and it is the only one
in scope. Evaluating skills you do not author, and re-materializing a past run
from its snapshots, are separate questions that this does not answer.

## Considered options

- **Shell out to `npx skills add`** (vercel-labs/skills, the "open agent skills
  ecosystem"). The obvious buy-don't-build option, and it looks like a close
  fit: its `skills-lock.json` is keyed by frontmatter `name:` — the same
  identity caliper installs under ([[install-and-discover]]) — and it records a
  `computedHash`, a sha256 over the sorted `(relpath, content)` of the whole
  skill folder. Rejected on three findings against v1.5.21:

  1. **Its `ref` is the string you typed, not a commit.** A lock entry for
     `owner/repo` with no ref means "the default branch, whenever `add` last
     ran". Adopting it as our pin would reimport the exact silent drift this
     work exists to remove.
  2. **Restore does not verify.** `experimental_install` re-runs `add` from the
     recorded source; `computedHash` is written and never checked, so a restored
     neighbourhood can differ from the recorded one with nothing saying so.
  3. **It installs into agent skill roots** (`.agents/skills/`,
     `.claude/skills/`, …) — a layout `install_skills` already owns for the
     isolated home, so the two installers would have to be reconciled.

  Smaller costs, none decisive alone: node/npx becomes a hard runtime dependency
  of a Python tool whose only shell-out today is `git`; both project-lock
  commands are still named `experimental_`; and `add` emits install telemetry
  (honors `DO_NOT_TRACK`), which caliper would be sending on a user's behalf.

- **Read a `skills-lock.json` beside the spec and resolve names to
  already-installed directories**, leaving the fetching to the user. Cheapest
  option and it composes with the ecosystem, but it inherits findings 1 and 2
  above, and it makes "run this spec" a two-command ritual — a spec stops being
  self-contained, which is most of what a git source is for.

## Consequences

- **`SkillSnapshot.git_sha` becomes trustworthy for skills caliper did not
  author.** The field already exists and is already populated, by running
  `git rev-parse HEAD` in whatever directory the path happened to live in. For a
  fetched skill it is now the commit caliper resolved and cloned, which is what
  makes [[skill drift]] detectable at all — see
  [0017](0017-unpinned-git-sources-are-allowed-because-drift-is-reported.md).
- **We gain no registry and no discovery.** `npx skills find` and skills.sh have
  no equivalent here; a spec author names a repo they already know. This is a
  real loss and the honest price of the decision. It is also recoverable —
  nothing about `{repo:, ref:, path:}` prevents a later resolver that turns a
  registry name into one.
- **One entry is one skill.** Declaring five members of a pack is five entries
  repeating `repo:`/`ref:`; entries sharing a repo and resolved commit share one
  clone, so the repetition costs bytes in the spec and nothing at run time. The
  alternative — one entry naming a whole repo, its skills discovered — was
  rejected because it makes the neighbourhood unreadable from the spec, and
  [[activation]] is a measurement only because "the set of things the agent
  could possibly have reached for is exactly the set the spec wrote down". It
  would also put `validate_activates` behind the network: "declared" would stop
  being knowable offline.
- **A bare string stays a path source forever.** The entry shape is a union, not
  a migration: `- ./SKILL.md` is untouched, and the mapping form mirrors
  `McpServer`'s existing local-vs-remote discrimination rather than inventing a
  URL grammar whose delimiters (`@`, `:`) collide with real branch names and
  paths.
- **`run` fetches; `validate` stays offline and schema-only.** The fetch happens
  before the first attempt, so an unreachable `repo:` fails at zero spend
  anyway, and `validate` keeps working on a plane.

# A git source may name its skill instead of its path

The skills ecosystem names a skill `owner/repo@name` (`npx skills add`,
skills.sh). A git source asked for the path of its `SKILL.md` instead, and that
path often doesn't match the name, so adding a neighbour meant browsing the repo.
A git source now takes `skill:`, the frontmatter `name:` to find, as an
alternative to `path:`. The clone, the cache and `ref:` are unchanged
(docs/adr/0016, docs/adr/0017), and one entry is still one skill.

Caliper searches the whole clone rather than copying `npx skills`' ordered list
of skill directories. That list is another tool's policy and would drift with
it. A shallower match shadows a deeper one, which keeps the property that
matters: a copy vendored under an agent directory never competes with the
pack's own. Two matches at one depth, or none, refuse the run. A silent pick
would measure against competition the spec didn't ask for, the same reason a
missing member refuses.

The resolved path is recorded in the snapshot (`git_path`), and `compare`
reports a change of it as drift, since a `skill:` entry follows a skill that
moved within its repo.

## Considered options

- **Shell out to `npx skills add`.** Rejected for the reasons in ADR 0016,
  which still hold on v1.7.2: its lock records no commit.
- **Accept `owner/repo@skill` as a string.** Deferred. `@` collides with branch
  names, and a bare string is a path source (ADR 0016).

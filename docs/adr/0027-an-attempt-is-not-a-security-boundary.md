# An attempt is not a security boundary

An attempt's isolation (a fresh home, the closed skill neighbourhood, no
account connectors) exists to keep a **measurement** clean: the agent sees the
spec's skills and servers and nothing ambient, so a score does not depend on
whose machine ran it. It does not contain a hostile skill. The agent runs as the
user, with the user's filesystem, and a skill can tell it to read any host path.
Evaluating a skill means running it, with the same trust as installing it.

This was written down because it was nearly reversed by accident. #145 treated a
skill's symlinks as an exfiltration path and #155 stopped installing any link
that pointed outside the skill directory. That closed one door in a building
with no walls, and broke a supported way of sharing files between skills.

## Symlinks follow real installs

A file symlink inside a skill directory is installed by copying its target's
bytes under the link's name, wherever the target lives. A real agent that
installs such a skill follows the link, so dropping it would give the agent a
broken reference it never meets in real use. The snapshot records those bytes,
so an edit to a shared target shows as drift (#104).

## Exclusions apply to the target too

A link is judged by its own path **and** its target's against the install
exclusions (`sandbox.forbidden_files`, `.git` and the other excluded
directories, `.eval.yaml`). Otherwise `hint.md -> answers/key.md` delivers the
answer key under a name no pattern matches, and the cheat check never sees it.
This is a measurement rule, not containment: `forbidden_files` keeps answer keys
out of the run. A target outside the skill directory is matched by its absolute
path, the same way `forbidden_files` matches paths in a transcript.

## Git sources stay inside their clone

A git source whose skill has a symlink pointing outside the cloned repo is
refused. Its bytes would come from whatever machine runs the eval, so the pinned
commit would no longer say what was installed. A link to a shared file elsewhere
in the same repo is fine. Path sources make no reproducibility claim (see
docs/adr/0017) and are not checked.

## Consequences

- Real containment (a filesystem sandbox around the agent) would be a new
  decision that supersedes this one, not a patch to install.
- A skill whose shared file lives outside its repo can't be used as a git
  source until the file is moved into the repo.

# Containment is delegated to a container runtime

> **Supersedes in part [0027](0027-an-attempt-is-not-a-security-boundary.md)**,
> which said real containment "would be a new decision that supersedes this
> one". An attempt run with `--container` is a security boundary for the
> agent; one run without it is still not.

Deciding whether to install a skill you did not write means running it, and
running it under caliper used to give it your environment: your files, your
network, every secret your shell holds (0027). A trust report produced that way
proves least about the skills it most needs to judge.

Caliper does not build a sandbox. With `--container IMAGE` it hands each
**agent** spawn to a Docker-compatible runtime and makes the container the
boundary. The attempt's temp dir (isolated home and workdir) is mounted at the
same path, so every path a harness wrote into a config still resolves; the
spec's `extra_path` directories are mounted read-only; nothing else of the host
is. The agent runs as the invoking user with every capability dropped and no
privilege escalation. Its network is a fresh `--internal` one: no route out,
and one reachable address, the host's side of that network, where the attempt's
egress proxy listens. The proxy tunnels to the hosts the run allows and refuses
the rest, so the egress log is complete and the policy holds.

## Why a container runtime, and not a sandbox of our own

- **Maintenance.** Per-platform profiles (bubblewrap, Seatbelt, AppContainer)
  are a second product. #158 reached the same conclusion for judge scripts.
- **Network.** An internal network plus a host-side proxy gives an enforced
  allow-list and a log with stdlib code. A process sandbox needs a second
  mechanism for each.
- **Reach.** Docker is what security reviewers already trust and already have.

## Trade-offs accepted

- **Only the agent is contained.** `setup:`, `assert:`, `cleanup:` and the
  judge are the spec author's code and stay on the host. Judge-written scripts
  remain #158's.
- **The agent's own login travels with it.** Credentials reach the container
  the way they reach any attempt, seeded into the isolated home. A skill can
  still use that one account; the trust report says so every time.
- **Linux with Docker Engine only, for now.** The proxy must listen on the
  internal network's gateway, a host interface on Linux and inside a VM on
  Docker Desktop. Caliper checks this before the first attempt and refuses.
- **The image is the user's.** It must carry the agent CLI; `docker/Dockerfile`
  is a starting point, not a pinned dependency. A stdio `mcp:` server starts
  inside it, so its host preflight is skipped.
- **Cleanup is explicit.** Killing the runtime's client on a timeout or Ctrl-C
  does not stop the container it started, so every spawn ends with a forced
  remove, and the run's network is removed when the run ends.

## Consequences

- `RunMeta.containment` records `docker:<image>`, and `compare` warns when two
  runs differ in it.
- An uncontained run can still watch egress (`sandbox.egress`), but the log is
  advisory and the report says so: a process that ignores the proxy variables
  goes around it.
- `caliper vet` runs its probes only with `--container`. Without one it scans
  the files and reports that behaviour was not observed.

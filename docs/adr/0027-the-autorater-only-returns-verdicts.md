# The autorater only returns verdicts

An `expect:` autorater used to choose between a direct verdict and a Python
script it wrote itself. Caliper executed that script with the caller's
permissions. The transcript includes agent-controlled text, so a skill or
agent under test could steer the judge into writing code that touched the host.

The autorater now returns only a direct pass/fail verdict. A response in
`script` mode is a judge error and its code is never executed. Deterministic
checks belong in the eval author's static `assert:` field, which continues to
run in the attempt workdir. If both `expect:` and `assert:` are present, a
malformed autorater response does not discard the authored assertion's verdict.

Sandboxing judge-written scripts would preserve artifact checks, but would
require a cross-platform isolation boundary, no network, and an audit record
of every generated script. Removing the mode avoids that execution path and
keeps the judge contract small.

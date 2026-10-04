"""What a run can say about a skill it does not trust (docs/CONTEXT.md → Trust).

Four parts, each usable on its own:

- :mod:`caliper.trust.canary` plants fake secrets in an attempt and reads a
  transcript for any sign the agent touched them.
- :mod:`caliper.trust.egress` is a logging forward proxy that allows only the
  hosts a run declares.
- :mod:`caliper.trust.container` runs the agent inside a container runtime, the
  external sandbox that makes the other two hold (docs/adr/0035).
- :mod:`caliper.trust.scan` reads a skill's files without running them, and
  :mod:`caliper.trust.report` combines that with a run into one trust report.
"""

# A judge script runs without the operator's environment

A script the autorater writes is model-written code shaped by the transcript,
so the agent or anything it read can steer it. It runs in the attempt workdir
like an `assert:`, but not with an author's trust: Caliper starts the base
interpreter in isolated mode (`python -I`, outside its own venv), with only
`PATH`, locale and `SYSTEMROOT` from the host, a throwaway `HOME` and temp dir,
and `CALIPER_WORKDIR`. It gets no `CALIPER_SPEC_DIR`, since the spec's
directory can hold the answer key.

This costs a check nothing it legitimately needs: the judge prompt already
promises the standard library and the workdir, and nothing more. Authored
`assert:` scripts keep the developer's full environment and the spec
directory, because their author chose what they run.

This is not confinement. The script still reads and writes the operator's
filesystem and reaches the network; closing that is the job of an external
sandbox (issue #158), which this does not replace.

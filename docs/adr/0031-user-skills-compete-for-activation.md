# User skills compete for activation

The user-layer extension in #177 follows
[0028](0028-runs-load-user-customizations-by-default.md).

User skills are copied into the native
skills root and join the observed activation set. Extra user-skill activations
fail exact `activates:` checks; expected names still have to be declared. The
closed-neighbourhood premise of 0014 now applies only to isolated runs. A
name declared by the spec is reserved even when ablated, so a user installation
cannot silently restore it. Claude plugin skills retain their namespace and can
be observed through named tool calls or reads of their staged skill files.
Skills the CLI ships in the user's skills root (hidden folders such as Codex's
`.system`, Hermes' `.bundled_manifest`) are not the user's and are not copied.
Skills install flat, so of two same-named user skills the first in sorted path
order wins rather than aborting the run.

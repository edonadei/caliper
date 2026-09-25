# User hooks run without preflight

The user-layer extension in #177 follows
[0028](0028-runs-load-user-customizations-by-default.md).

Settings and plugins may contain hooks, environment
variables and permissions. The CLI's noninteractive flags still win. Hooks run
under the attempt timeout, without preflight: a probe would execute side effects
twice. Absolute paths authored in settings are preserved, consistent with 0027;
isolation is not a security boundary.

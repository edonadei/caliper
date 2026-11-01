# Usage telemetry

Caliper's optional usage telemetry helps maintainers understand which commands
and backends people use, and which commands fail. It is separate from the
evaluation scores, token usage and timings in saved results. It does not upload
results files or change the results schema.

## Consent and controls

Collection is off by default. No consent prompt appears during commands or in
automation. Opt in explicitly:

```bash
caliper telemetry enable
caliper telemetry status
caliper telemetry disable
```

The preference is stored atomically in `telemetry.json` in Caliper's per-user
config directory, beside its ChatGPT login. It contains only `{"enabled": true}`
or `{"enabled": false}`. The directory is `$XDG_CONFIG_HOME/caliper`, falling
back to `~/.config/caliper`, or `%APPDATA%\caliper` on Windows. A missing,
unreadable or malformed preference disables collection.

Any nonempty `CALIPER_TELEMETRY_DISABLED` overrides a saved opt-in. For example:

```bash
CALIPER_TELEMETRY_DISABLED=1 caliper run my-skill.eval.yaml
```

Consent commands never send an event. Help, shell completion, unknown commands
and argument-parsing errors also send nothing. Product commands send at most
one event when their command callback finishes or raises. An abrupt process
termination, including a forced kill, may prevent delivery.

## Payload

The event name is `caliper_command_completed`. Its only usage properties are:

| Property | Values |
| --- | --- |
| `schema_version` | `1` |
| `command` | `run`, `report`, `compare`, `list`, `validate`, `login`, `logout` |
| `caliper_version` | Numeric release version, optionally with an `a`, `b` or `rc` suffix; `unknown` for other versions |
| `os_family` | `macos`, `windows`, `linux`, `other` |
| `backend` | Selected run backend: `claude-code`, `codex`, `hermes`, `pi`; `null` for other commands or an unknown backend |
| `outcome` | `success`, `bad_input`, `cannot_run`, `bar_not_met`, `interrupted`, `error` |
| `duration_bucket` | `lt_1s`, `1s_10s`, `10s_60s`, `1m_10m`, `ge_10m` |

Durations cover callback execution, excluding argument parsing and telemetry
delivery. Bucket lower bounds are inclusive and upper bounds exclusive.
The backend is the selection made by `--model`, including its default and
aliases. It does not claim a backend actually launched, since preflight or
spec validation may fail first. The judge backend is not collected.

Outcomes map command exit codes `0`, `1`, `2`, `3`, `130` to `success`,
`bad_input`, `cannot_run`, `bar_not_met`, `interrupted`. Other exits and
unexpected exceptions are `error`; a Ctrl-C or abort is `interrupted`.
`success` describes the command, not the skill's score. See
[Exit codes](../README.md#exit-codes).

PostHog also requires a public project token and a `distinct_id`. Every
installation uses the same constant `caliper-anonymous`. Caliper never creates
or stores an installation, user, machine or session ID. Each event sets
`$process_person_profile: false`, `$geoip_disable: true` and `$ip: "0.0.0.0"`.
Its HTTP user agent is the constant `caliper-telemetry/1`.

No prompts, responses, transcripts, checks, scores, token usage, paths,
repository URLs, skill names, model names, MCP server names, credentials, raw
arguments, raw error messages or stack traces enter the payload. Local version
labels are discarded because they can contain a developer's name or branch.

## Delivery and privacy boundaries

The client uses a single HTTPS POST to PostHog's capture API with certificate
verification. It does not follow redirects, load an analytics SDK, capture
other events, retry, or persist a queue. A daemon worker isolates DNS and
network waits; the command waits at most 200 milliseconds for delivery after
the payload has been prepared. Slow delivery can be lost when the process
exits. All telemetry failures are silent and preserve command behavior.

PostHog receives a request timestamp and can see the connecting IP address at
the network layer. The placeholder IP and disabled GeoIP prevent Caliper from
supplying an actual IP or requesting location enrichment in event properties;
they do not make a direct HTTPS connection invisible to the provider. Configure
the project to discard IP data as described below. The provider's infrastructure
logs and retention remain governed by its policies. Do not promise that no
third party ever sees a network address.

These metrics describe participating command invocations. They cannot measure
unique users, returning users, installations, retention or total adoption.
Opt-in and best-effort delivery also mean command counts are incomplete.

## Maintainer setup in PostHog

The release is configured with Caliper's public ingestion token and US Cloud
host in `caliper/telemetry.py`. A public project token only permits ingestion;
it cannot query analytics. Never replace it with a private or personal API key.

Before collecting production metrics:

1. In PostHog's project settings, set IP data capture to **discard client IPs**.
   See [PostHog's IP data capture controls](https://posthog.com/docs/privacy/data-collection#ip-data-capture).
2. Set an appropriate event retention policy. Use the free plan or a billing
   limit if you want to cap spending. See [current pricing](https://posthog.com/product-analytics/pricing).
3. Create a product analytics trend for `caliper_command_completed`, measured
   by **total event count**, broken down by `command`. Add trends broken down
   by `backend`, `outcome`, `caliper_version`, `os_family` and `duration_bucket`
   as needed. Filter to `command = run` for backend comparisons.
4. Keep person identification, enrichment and identity-related transformations
   off for these events. Unique-user charts and funnels are not meaningful
   because the distinct ID is shared by everyone.

To test delivery, opt in from a disposable config directory and validate a
small spec. Check the event in PostHog's activity view and inspect its properties.
Do not enable collection in an actual user's config as a side effect of testing.

Forks can use `CALIPER_TELEMETRY_PROJECT_TOKEN` with their own public `phc_` token
and `CALIPER_TELEMETRY_HOST` with `https://us.i.posthog.com` or
`https://eu.i.posthog.com`. These settings never opt a user in. An empty or
invalid token, or any other host, disables delivery. `telemetry status` reports
an inactive destination without printing its token.

The ingestion token is public, so event counts can be spoofed. Use them to
inform product priorities, not as an audited adoption or billing measure.

## Querying metrics with an agent

Connect your agent to the [official PostHog MCP server](https://posthog.com/docs/model-context-protocol)
at `https://mcp.posthog.com/mcp` and authenticate to your PostHog account.
Choose the Caliper project. Authentication for reading metrics stays in your
agent's connection configuration, never in Caliper or its repository.

Example prompts:

- "Count Caliper command events over the last seven days, broken down by command."
- "For Caliper run events this week, show command outcomes by backend."
- "Compare Caliper command counts this week and last week. Use total events, not unique users."

The equivalent HogQL query for command counts is:

```sql
SELECT properties.command AS command, count() AS invocations
FROM events
WHERE event = 'caliper_command_completed'
  AND timestamp >= now() - INTERVAL 7 DAY
GROUP BY command
ORDER BY invocations DESC
```

The capture protocol is documented in [PostHog's capture API reference](https://posthog.com/docs/api/capture).

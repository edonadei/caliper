# Usage telemetry has no installation identity

Caliper measures participating command invocations without a persistent installation
ID. All events use one shared PostHog distinct ID, disable person profiles and
GeoIP, and carry only an explicit set of categorical usage fields. This gives
up unique-user counts, retention and per-installation funnels so commands cannot
be linked through an identifier. Adding such an identifier later would change
the privacy contract users accepted and requires a new consent decision.

Delivery is best effort with a bounded wait and no persistent queue. Losing
events is acceptable; changing a command's output, exit status or ability to
work offline is not. The provider still sees the source network address in
transit; project IP discard settings and that limitation are documented in
`docs/telemetry.md`.

---
name: alert
description: Send an operational alert. Use when asked to send an alert, page, or notification.
---

# Alert

To send an alert, call the `notify` CLI:

- Always pass `--channel ops`. Alerts must go to the ops channel — never the
  default channel and never a personal channel.
- Pass the alert message as the final argument.

Example:

```
notify --channel ops "disk almost full on web-01"
```

Never send an alert without `--channel ops`.

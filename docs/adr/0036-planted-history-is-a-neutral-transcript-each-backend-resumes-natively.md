# Planted history is a neutral transcript each backend resumes natively

Some skills can only be measured mid-conversation. A methodology skill such as
`poteto-mode` is entered by a human slash command (`disable-model-invocation:
true`), so install-and-discover (docs/adr/0013) can never load it. Many of its
playbooks also assume earlier turns: a plan already agreed, a file already read,
a fix already tried. Every attempt today starts at turn 1 with
`claude -p <prompt>` or the equivalent, so none of that is reachable.

A task may now declare a **planted history**: earlier turns the agent resumes
from, as if they had happened. The prompt becomes the next user turn.

## Native resume, not a pasted summary

Pasting the earlier turns into the prompt costs nothing to build, but the model
reads them as a quoted document, not as its own turns. It has no tool calls
behind it, and a skill invoked "earlier" was never loaded. Each backend CLI
already resumes a session from a file it stores, and a hand-written file is
accepted:

- **claude-code**: a JSONL session under the config dir's `projects/<cwd slug>/`,
  resumed with `--resume <id>`.
- **codex**: a rollout JSONL under `$CODEX_HOME/sessions/`, resumed with
  `codex exec resume <id>`. It is found by scanning, so no index has to be written.
- **pi**: any JSONL session file, passed as `--session <path>`.

All three were probed in an isolated home holding only the auth and config files
caliper already copies in. Each agent answered from a planted user turn and a
planted tool result. Each also trusted a planted tool result that contradicted
the file on disk.

claude-code ships first. Codex and pi follow with their own translators, and
until then a spec with `history:` is refused on them like on hermes.

## One neutral format, translated at the harness boundary

The spec carries a backend-neutral list of turns: user text, assistant text, and
a shell call with its output. Each harness translates it into its CLI's own
session format. This follows the backend-consistency stance of docs/adr/0003 and
docs/adr/0012. The same spec runs on any engine that can honor it, and the
CLIs' session formats stay out of the spec.

The shell call is the only planted tool in v1. Every backend has one, and it
maps one-to-one (`Bash`, `exec_command`, `bash`). Other planted tools can be
added later, but a tool removed from the format would break specs that use it.

The session file embeds the absolute working directory, and claude-code derives
its storage path from it. The file is therefore written per attempt, after the
attempt workdir exists (docs/adr/0026), never shipped prebuilt.

## Hermes refuses planted history

Hermes stores sessions in SQLite. Its only import path (`hermes sessions import
--from claude|codex`) is lossy. A tool call becomes the text `[ran tool: Bash]`,
its result is dropped, and consecutive assistant turns merge. The resumed
session also keeps `source: claude-code`, so the `sessions export --source cli`
trajectory read finds nothing. Writing straight into the SQLite store would mean
depending on an internal schema.

So a spec that declares `history:` fails on hermes as a hard error, as `mcp:`
does on pi (docs/adr/0010). Silently weakening the history would report a number
for a different task.

This may not be permanent. Two upstream changes are open, and neither has been
reviewed:

- NousResearch/hermes-agent#90433 (fix in #90741) keeps tool arguments and
  output on foreign import, but renders them as prose and builds no real tool
  calls. The content would survive, but not as tool turns.
- NousResearch/hermes-agent#8650 (PR #8651) imports hermes's own export format
  with real tool calls. That is the full-fidelity path: caliper would translate
  to the export format and import it. Triage notes the PR targets an outdated
  schema.

If #8650 lands, hermes can join with a translator like the other three backends.

## Consequences

- **The planted turns belong to the task, not to the agent.** They are excluded
  from the attempt's transcript, so the judge and cheat detection never grade
  them as the agent's behaviour. The judge receives them separately, labelled as
  prior context, because "finish what we agreed" can't be graded without them.
  On claude-code this costs nothing: the `stream-json` output of a resumed
  session does not replay the earlier turns.
- **Activation in the planted history is not activation.** A skill shown as
  loaded in a planted turn was put there by the author. Only skills the agent
  loads after resuming count toward `activates:`.
- **A planted skill is named, never pasted.** On claude-code, a planted user
  turn of `/poteto-mode` resumes as those words and loads nothing; a user turn
  holding the skill's body, as the CLI writes an expanded slash command, does.
  Pasting the body into the history would leave a copy that silently goes stale
  when the skill is edited. So a user turn opening with `/name` of a declared
  skill is a slash command, and each harness expands it from the installed copy
  at run time. The history reads as typed, with no extra turn kind. An ablated
  skill isn't installed, so the turn stays as typed, which is what a user
  typing it would get.
- **Planted observations override the disk.** The agent believes a planted tool
  result over the real file, so the history and `setup:` must agree. The
  authoring guidance says so.
- **The session formats are undocumented CLI internals.** They can change between
  CLI versions. Caliper writes only the minimal fields each probe needed (on
  claude-code 2.1.296, a line without a `timestamp` makes the session invisible
  to `--resume`), and a format break shows up as the CLI rejecting the resume,
  an `infra_error`, not a silent wrong score.

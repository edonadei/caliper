"""The held-out boundary for hillclimb-skill, kept in code.

Stdlib only, so it runs wherever the skill is copied. The agent never opens the
spec or a results JSON itself: it goes through these three commands, which show
held-out tasks as scores and nothing else.

    climb.py split <run.json> <state.json> --surface body|description [--skill NAME]
    caliper compare A B --format json | climb.py scores <state.json>
    climb.py failures <run.json> <state.json> [--task NAME]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# Below this, a third held out is one or two tasks: too few to say anything.
MIN_TASKS = 8
_CLIP = 600


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _clip(text: object) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= _CLIP else text[:_CLIP] + " […]"


def split(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    if state_path.exists():
        sys.exit(f"{state_path} exists: the split is fixed for the whole climb")
    if args.surface == "description" and not args.skill:
        sys.exit("--surface description needs --skill <frontmatter name>")

    # Stratified, so both halves hold "should fire" and "should stay silent".
    strata: dict[bool, list[str]] = {}
    for task in _load(args.run)["task_results"]:
        if args.surface == "description":
            expected = task.get("activation_expected")
            if expected is None:
                continue
            key = args.skill in expected
        else:
            if all(a["outcome"] == "not_checked" for a in task["attempts"]):
                continue
            key = True
        strata.setdefault(key, []).append(task["task_name"])

    total = sum(len(names) for names in strata.values())
    if total < MIN_TASKS:
        sys.exit(
            f"{total} tasks measure the {args.surface}; a climb needs {MIN_TASKS} "
            "to hold any out. Add tasks first."
        )

    seed = args.seed if args.seed is not None else random.randrange(10**6)
    rng = random.Random(seed)
    train: list[str] = []
    held_out: list[str] = []
    for key in sorted(strata):
        names = sorted(strata[key])
        rng.shuffle(names)
        cut = round(len(names) / 3) if len(names) > 1 else 0
        held_out += names[:cut]
        train += names[cut:]

    state = {
        "surface": args.surface,
        "skill": args.skill,
        "seed": seed,
        "train": train,
        "held_out": held_out,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"split written: {len(train)} train, {len(held_out)} held-out")


def scores(args: argparse.Namespace) -> None:
    state = _load(args.state)
    comp = json.load(sys.stdin)
    field = "activation" if state["surface"] == "description" else "score"
    by_name = {t["task_name"]: t for t in comp["matched"]}

    for label in ("train", "held_out"):
        pairs = [
            (by_name[n][f"a_{field}"], by_name[n][f"b_{field}"])
            for n in state[label]
            if n in by_name
        ]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        name = label.replace("_", "-")
        if not pairs:
            print(f"{name:<9} no task measured on both sides")
            continue
        a = sum(p[0] for p in pairs) / len(pairs)
        b = sum(p[1] for p in pairs) / len(pairs)
        print(
            f"{name:<9} {a:.0%} -> {b:.0%}  ({(b - a) * 100:+.0f} pp)  "
            f"{len(pairs)}/{len(state[label])} tasks measured"
        )

    a_tok = comp["a_usage"].get("tokens_per_attempt")
    b_tok = comp["b_usage"].get("tokens_per_attempt")
    if a_tok and b_tok is not None:
        print(f"tokens/attempt  {a_tok} -> {b_tok}  ({(b_tok - a_tok) / a_tok:+.0%})")
    for warning in comp.get("warnings", []):
        print(f"warning: {warning}")


def failures(args: argparse.Namespace) -> None:
    state = _load(args.state)
    train = set(state["train"])
    if args.task and args.task not in train:
        sys.exit(f"{args.task!r} is not a train task")
    by_description = state["surface"] == "description"

    shown = 0
    for task in _load(args.run)["task_results"]:
        name = task["task_name"]
        if name not in train or (args.task and name != args.task):
            continue
        attempts = task["attempts"]
        if by_description:
            failed = [a for a in attempts if a.get("activation_passed") is False]
        else:
            failed = [a for a in attempts if a["outcome"] in ("task_fail", "cheat")]
        if not failed:
            continue

        shown += 1
        print(f"## {name}  ({len(failed)}/{len(attempts)} attempts failed)")
        print(f"prompt: {_clip(task.get('prompt'))}")
        if by_description:
            print(f"expected to fire: {task.get('activation_expected')}")
        else:
            print(f"expect: {_clip(task.get('expect'))}")
        for a in failed:
            print(
                f"- attempt {a['attempt']}: {a['outcome']}, fired {a.get('activated')}"
            )
            if by_description:
                continue
            for field in ("autorater_reasoning", "assert_evidence"):
                if a.get(field):
                    print(f"  {field}: {_clip(a[field])}")
            if a.get("cheat_evidence"):
                print(f"  cheat_evidence: {a['cheat_evidence']}")
            if args.task:
                for turn in a.get("transcript") or []:
                    tool = f" [{turn['tool_name']}]" if turn.get("tool_name") else ""
                    body = turn.get("content") or turn.get("tool_input")
                    print(f"    {turn['role']}{tool}: {_clip(body)}")
                    if turn.get("tool_output"):
                        print(f"      -> {_clip(turn['tool_output'])}")
        print()

    if not shown:
        print("no train failures")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("split", help="fix the train / held-out split")
    p.add_argument("run")
    p.add_argument("state")
    p.add_argument("--surface", choices=("body", "description"), required=True)
    p.add_argument("--skill", help="frontmatter name; required for description")
    p.add_argument("--seed", type=int)
    p.set_defaults(func=split)

    p = sub.add_parser("scores", help="per-split scores from compare JSON on stdin")
    p.add_argument("state")
    p.set_defaults(func=scores)

    p = sub.add_parser("failures", help="failing attempts of train tasks only")
    p.add_argument("run")
    p.add_argument("state")
    p.add_argument("--task", help="one train task, with its transcripts")
    p.set_defaults(func=failures)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render the README's animated hero to SVG, in a dark and a light variant.

The hero plays the ablation story the README opens with: run the eval without
the skill, run it with the skill, compare. Each attempt lands as a mark, so the
reader watches the bare agent's misses next to the skill's clean column.

It's hand-built SVG rather than a rich export (see render_readme_samples.py)
because GitHub strips scripts and styles from a README, and CSS keyframes
inside an SVG are the one kind of motion an `<img>` still plays. Every element
rests in its final state, so `prefers-reduced-motion` (and any renderer that
skips the animation) shows the finished comparison.

The numbers match the compare-ablation.svg fixture. Regenerate after changing
them:

    python docs/render_readme_hero.py
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ASSETS = Path(__file__).resolve().parent / "assets"

PALETTES = {
    "dark": {
        "window": "#1e1f22",
        "bar": "#2a2b2f",
        "rule": "#3a3c41",
        "fg": "#d4d6d4",
        "dim": "#8a8d8c",
        "prompt": "#6fb3c9",
        "pass": "#9ec25a",
        "fail": "#e0676c",
        "glow": "#9ec25a",
        "cursor": "#d4d6d4",
    },
    "light": {
        "window": "#fbfbf9",
        "bar": "#efefeb",
        "rule": "#dcdcd6",
        "fg": "#1f2328",
        "dim": "#656d76",
        "prompt": "#0a6f8f",
        "pass": "#2f7d1e",
        "fail": "#c2323a",
        "glow": "#2f7d1e",
        "cursor": "#1f2328",
    },
}

LOOP = 17.0  # seconds
# The finished comparison holds until here, then fades for the restart.
FADE_OUT = round(16.4 / LOOP * 100, 3)  # % of the loop

WIDTH = 900
PAD = 28
FONT = 15
CHAR = FONT * 0.602  # advance of a typical monospace face

COMMANDS = [
    "caliper run inbox-triage.eval.yaml --k 5 --ablate inbox-triage",
    "caliper run inbox-triage.eval.yaml --k 5",
    "caliper compare .caliper/results/inbox-triage/RUN_ID.json inbox-triage",
]
TASKS = [
    # name, without-skill attempts, with-skill attempts, delta
    ("Flags emails that need a reply", "PPPPP", "PPPPP", "—"),
    ("Drafts replies, never sends them", "PFPFP", "PPPPP", "+40 pp"),
    ("Skips no-reply senders", "PPFPP", "PPPPP", "+20 pp"),
    ("Resists a prompt injection", "PPPPF", "PPPPP", "+20 pp"),
]
SUMMARY = [
    # label, A, A detail, B, B detail, delta, delta is a win
    ("Success", "80%", "16/20", "100%", "20/20", "+20 pp", True),
    ("Tokens", "612K", "", "431K", "", "-30%", True),
    ("Wall", "4m 22s", "", "3m 8s", "", "-28%", True),
]

# Column geometry
COL_A = 372
COL_B = 584
MARK_GAP = 18
PCT_A = COL_A + 5 * MARK_GAP + 56
PCT_B = COL_B + 5 * MARK_GAP + 56
COL_D = WIDTH - PAD

# Row baselines
Y_CMD = [76, 102, 128]
Y_HEAD = 172
Y_RULE1 = 184
Y_ROWS = [208, 234, 260, 286]
Y_RULE2 = 304
Y_SUM = [330, 356, 382]
HEIGHT = 412

# Timeline (seconds)
T_TYPE = [(0.5, 2.1), (5.2, 6.2), (9.4, 11.0)]
T_A_MARKS = (2.4, 4.6)
T_B_MARKS = (6.5, 8.7)
T_COMPARE = 11.3


def pct(t: float) -> float:
    return round(t / LOOP * 100, 3)


class Sheet:
    """Collects one keyframe block per distinct timing, keyed by class name."""

    def __init__(self) -> None:
        self.rules: dict[str, str] = {}

    def appear(self, t: float, pop: bool = False) -> str:
        name = f"{'p' if pop else 'a'}{int(t * 100)}"
        if name not in self.rules:
            p = pct(t)
            q = round(p + (2.2 if pop else 3.0), 3)
            if pop:
                frames = (
                    f"0%,{p}%{{opacity:0;transform:scale(.3)}}"
                    f"{q}%{{opacity:1;transform:scale(1.25)}}"
                    f"{round(q + 1.6, 3)}%,{FADE_OUT}%{{opacity:1;transform:scale(1)}}"
                    f"100%{{opacity:0;transform:scale(1)}}"
                )
                timing = "cubic-bezier(.16,1,.3,1)"
            else:
                frames = f"0%,{p}%{{opacity:0}}{q}%,{FADE_OUT}%{{opacity:1}}100%{{opacity:0}}"
                timing = "cubic-bezier(.16,1,.3,1)"
            self.rules[name] = (
                f"@keyframes {name}{{{frames}}}"
                f".{name}{{animation:{name} {LOOP}s {timing} infinite}}"
            )
        return name

    def typing(self, i: int, t0: float, t1: float, chars: int) -> str:
        """A cover that slides off the command one character at a time."""
        name = f"t{i}"
        dist = round(chars * CHAR + CHAR, 2)
        p0, p1 = pct(t0), pct(t1)
        self.rules[name] = (
            f"@keyframes {name}{{"
            f"0%,{p0}%{{transform:translateX(0);opacity:1;"
            f"animation-timing-function:steps({chars},end)}}"
            f"{p1}%{{transform:translateX({dist}px);opacity:1}}"
            f"{round(p1 + 0.01, 3)}%,100%{{transform:translateX({dist}px);opacity:0}}}}"
            f".{name}{{opacity:0;transform:translateX({dist}px);"
            f"animation:{name} {LOOP}s linear infinite}}"
        )
        return name

    def css(self) -> str:
        return "".join(self.rules.values())


def text(x, y, s, cls="fg", anchor=None, extra=""):
    if not s:
        return ""
    a = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text x="{x}" y="{y}" class="{cls}"{a}{extra}>{escape(s)}</text>'


def mark(cx: float, cy: float, passed: bool) -> str:
    if passed:
        d = f"M{cx - 4.5} {cy + 0.2}l3.2 3.3 5.8-6.6"
        return f'<path d="{d}" class="ok"/>'
    d = f"M{cx - 3.8} {cy - 3.8}l7.6 7.6m0-7.6l-7.6 7.6"
    return f'<path d="{d}" class="no"/>'


def render(theme: str) -> str:
    c = PALETTES[theme]
    sheet = Sheet()
    body: list[str] = []

    # Window chrome
    body.append(
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="12" fill="{c["window"]}"/>'
        f'<path d="M0 12a12 12 0 0 1 12-12h{WIDTH - 24}a12 12 0 0 1 12 12v28H0z" '
        f'fill="{c["bar"]}"/>'
        f'<circle cx="24" cy="20" r="6" fill="#ff5f57"/>'
        f'<circle cx="44" cy="20" r="6" fill="#febc2e"/>'
        f'<circle cx="64" cy="20" r="6" fill="#28c840"/>'
        + text(WIDTH / 2, 25, "caliper", "dim", "middle", ' font-size="13"')
    )

    # Commands, typed one after another
    labels = [("→ run A", T_A_MARKS[1] + 0.2), ("→ run B", T_B_MARKS[1] + 0.2)]
    for i, (cmd, y, (t0, t1)) in enumerate(zip(COMMANDS, Y_CMD, T_TYPE, strict=True)):
        start = PAD + 2 * CHAR
        line = (
            text(PAD, y, "$", "pr")
            + text(start, y, cmd)
            + f'<g class="{sheet.typing(i, t0, t1, len(cmd))}">'
            f'<rect x="{start}" y="{y - FONT}" width="{CHAR}" height="{FONT + 4}" '
            f'fill="{c["cursor"]}"/>'
            f'<rect x="{start + CHAR}" y="{y - FONT}" '
            f'width="{len(cmd) * CHAR + CHAR}" height="{FONT + 4}" fill="{c["window"]}"/>'
            f"</g>"
        )
        body.append(f'<g class="{sheet.appear(t0 - 0.3)}">{line}</g>')
        if i < len(labels):
            label, t = labels[i]
            body.append(
                f'<g class="{sheet.appear(t)}">'
                + text(COL_D, y, label, "ok-t", "end")
                + "</g>"
            )

    # Table header and rules
    head_a = sheet.appear(T_A_MARKS[0] - 0.2)
    head_b = sheet.appear(T_B_MARKS[0] - 0.2)
    head_d = sheet.appear(T_COMPARE)
    body.append(
        f'<g class="{head_a}">'
        + text(PAD, Y_HEAD, "Task", "dim b")
        + text(COL_A, Y_HEAD, "without inbox-triage", "dim b")
        + f'<path d="M{PAD} {Y_RULE1}H{COL_D}" class="rule"/>'
        + "</g>"
        + f'<g class="{head_b}">'
        + text(COL_B, Y_HEAD, "with inbox-triage", "dim b")
        + "</g>"
        + f'<g class="{head_d}">'
        + text(COL_D, Y_HEAD, "Δ", "dim b", "end")
        + f'<path d="M{PAD} {Y_RULE2}H{COL_D}" class="rule"/>'
        + "</g>"
    )

    # Attempts land column by column: every task's first attempt, then the
    # second, the way parallel workers finish them.
    def stamp(column: int, runs: str, y: float, window: tuple[float, float], i: int):
        t0, t1 = window
        step = (t1 - t0) / 20
        out = []
        for k, outcome in enumerate(runs):
            t = t0 + (k * 4 + i) * step
            cx = column + k * MARK_GAP + 6
            out.append(
                f'<g class="{sheet.appear(t, pop=True)} m">'
                + mark(cx, y - 5, outcome == "P")
                + "</g>"
            )
        return "".join(out)

    for i, ((name, a, b, delta), y) in enumerate(zip(TASKS, Y_ROWS, strict=True)):
        a_rate = f"{a.count('P') * 20}%"
        b_rate = f"{b.count('P') * 20}%"
        body.append(f'<g class="{head_a}">' + text(PAD, y, name) + "</g>")
        body.append(stamp(COL_A, a, y, T_A_MARKS, i))
        body.append(stamp(COL_B, b, y, T_B_MARKS, i))
        body.append(
            f'<g class="{sheet.appear(T_A_MARKS[1])}">'
            + text(PCT_A, y, a_rate, "fg" if a == "PPPPP" else "no-t", "end")
            + "</g>"
            + f'<g class="{sheet.appear(T_B_MARKS[1])}">'
            + text(PCT_B, y, b_rate, "fg", "end")
            + "</g>"
            + f'<g class="{sheet.appear(T_COMPARE + 0.1 * i)}">'
            + text(COL_D, y, delta, "ok-t b" if delta != "—" else "dim", "end")
            + "</g>"
        )

    # The one authored moment: the success row lights up once the diff lands.
    t_glow = T_COMPARE + 0.9
    body.append(
        f'<g class="{sheet.appear(t_glow)}">'
        f'<rect x="{PAD - 10}" y="{Y_SUM[0] - 18}" width="{WIDTH - 2 * PAD + 20}" '
        f'height="26" rx="5" fill="{c["glow"]}" fill-opacity=".13"/></g>'
    )
    for i, ((label, a, a_sub, b, b_sub, delta, win), y) in enumerate(
        zip(SUMMARY, Y_SUM, strict=True)
    ):
        weight = " b" if i == 0 else ""
        body.append(
            f'<g class="{sheet.appear(T_COMPARE + 0.4 + 0.15 * i)}">'
            + text(PAD, y, label, "fg" + weight)
            + text(COL_A, y, a_sub, "dim")
            + text(PCT_A, y, a, "fg" + weight, "end")
            + text(COL_B, y, b_sub, "dim")
            + text(PCT_B, y, b, "fg" + weight, "end")
            + text(COL_D, y, delta, ("ok-t" if win else "no-t") + weight, "end")
            + "</g>"
        )

    style = (
        "text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"
        f'"DejaVu Sans Mono",monospace;font-size:{FONT}px;white-space:pre}}'
        f".fg{{fill:{c['fg']}}}.dim{{fill:{c['dim']}}}.pr{{fill:{c['prompt']}}}"
        f".ok-t{{fill:{c['pass']}}}.no-t{{fill:{c['fail']}}}.b{{font-weight:700}}"
        f".rule{{stroke:{c['rule']};stroke-width:1}}"
        "path.ok,path.no{fill:none;stroke-width:2.2;stroke-linecap:round;"
        "stroke-linejoin:round}"
        f"path.ok{{stroke:{c['pass']}}}path.no{{stroke:{c['fail']}}}"
        ".m{transform-box:fill-box;transform-origin:center}"
        + sheet.css()
        + "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
    )
    alt = (
        "caliper run of inbox-triage at k=5 without the skill, then with it, "
        "then caliper compare. Without the skill: 80% success (16/20). "
        "With it: 100% (20/20), +20 pp, 30% fewer tokens, 28% less wall time."
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(alt)}">'
        f"<title>{escape(alt)}</title><style>{style}</style>"
        + "".join(body)
        + "</svg>\n"
    )


def main() -> None:
    for theme in PALETTES:
        out = ASSETS / f"hero-{theme}.svg"
        out.write_text(render(theme), encoding="utf-8")
        print(f"wrote {out.relative_to(ASSETS.parent.parent)}")


if __name__ == "__main__":
    main()

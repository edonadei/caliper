"""GitHub-flavoured Markdown for `report` and `compare` (``--format markdown``).

The shape a pull-request comment or a job summary wants: the verdict first,
then one table, then the warnings. Deliberately smaller than the terminal view —
a reviewer reads a comment for the decision and follows the artifact for the
rest — and built from the same derived values, so the two cannot disagree.
"""

from __future__ import annotations

from caliper.gate import GateResult, RateCheck, Verdict, evaluate
from caliper.schema.results import RunComparison, RunMeta, RunResults

_VERDICT = {
    Verdict.CLEARED: "✅ cleared",
    Verdict.MISSED: "❌ missed",
    Verdict.INCONCLUSIVE: "⚠️ inconclusive",
    Verdict.NOT_APPLIED: "➖ not applied",
}


def _pct(rate: float | None) -> str:
    if rate is None:
        return "—"
    pct = rate * 100
    return f"{pct:.0f}%" if abs(pct - round(pct)) < 0.05 else f"{pct:.1f}%"


def _pp(delta: float | None) -> str:
    if delta is None or abs(delta) < 1e-9:
        return "—"
    return f"{delta * 100:+.0f} pp"


def _cell(text: str) -> str:
    # A literal pipe would split the cell; a newline would end the row.
    return text.replace("|", "\\|").replace("\n", " ")


def _engine(meta: RunMeta) -> str:
    return f"{meta.backend} · {meta.model}" if meta.model else meta.backend


def _check_line(check: RateCheck) -> str:
    return (
        f"- {_VERDICT[check.verdict]} **{check.name}** {_pct(check.rate)} "
        f"({check.successes}/{check.usable}, 95% CI {_pct(check.low)}–"
        f"{_pct(check.high)}) against a bar of {_pct(check.bar)}"
    )


def gate_markdown(gate: GateResult | None) -> list[str]:
    if gate is None:
        return ["**Bar:** none declared — report only."]
    lines = [f"**Bar:** {_VERDICT[gate.verdict]}"]
    if gate.reason:
        lines[0] += f" — {gate.reason}"
    elif gate.verdict is Verdict.INCONCLUSIVE:
        lines[0] += (
            " — blocking (`on_inconclusive: fail`)"
            if gate.blocks
            else " — the interval straddles the bar; a larger `k` would settle it"
        )
    lines += [_check_line(check) for check in gate.checks]
    return lines


def run_markdown(results: RunResults) -> str:
    run, agg = results.run, results.aggregate
    lines = [
        f"### `{run.spec}` · k={run.k} · {_engine(run)}",
        "",
        *gate_markdown(evaluate(results)),
        "",
    ]
    show_activation = any(
        t.activation_expected is not None for t in results.task_results
    )
    header = "| Task | success |" + (" activation |" if show_activation else "")
    rule = "|---|---|" + ("---|" if show_activation else "")
    lines += [header, rule]
    for task in results.task_results:
        row = f"| {_cell(task.task_name)} | {_pct(task.score)} ({task.successes}/{task.usable}) |"
        if show_activation:
            act = (
                f"{_pct(task.activation_score)} "
                f"({task.activation_successes}/{task.activation_usable})"
                if task.activation_expected is not None
                else "—"
            )
            row += f" {act} |"
        lines.append(row)
    overall = f"| **Overall** | **{_pct(agg.avg_score) if agg.measured else '—'}** |"
    if show_activation:
        overall += f" **{_pct(agg.avg_activation_score)}** |"
    lines.append(overall)
    noise = results.noise_counts
    if noise:
        breakdown = ", ".join(f"{n} {o.value}" for o, n in noise.items())
        lines += ["", f"Unusable attempts (excluded from the score): {breakdown}."]
    return "\n".join(lines) + "\n"


def comparison_markdown(comp: RunComparison) -> str:
    lines = [
        f"### `{comp.b.spec}`: {_pct(comp.a_matched_avg)} → "
        f"{_pct(comp.b_matched_avg)} ({_pp(comp.aggregate_delta)})",
        "",
        "| Task | A | B | Δ |",
        "|---|---|---|---|",
    ]
    for task in comp.matched:
        flag = " 🔻" if task.regression else ""
        lines.append(
            f"| {_cell(task.task_name)} | {_pct(task.a_score)} | "
            f"{_pct(task.b_score)} | {_pp(task.delta)}{flag} |"
        )
    if comp.unmatched_a or comp.unmatched_b:
        lines.append("")
        if comp.unmatched_a:
            lines.append(f"Only in A: {', '.join(comp.unmatched_a)}")
        if comp.unmatched_b:
            lines.append(f"Only in B: {', '.join(comp.unmatched_b)}")
    if comp.warnings:
        lines += ["", *(f"> ⚠️ {w}" for w in comp.warnings)]
    lines += [
        "",
        "_A regression flag (🔻) is any drop at all, so it fires on noise at "
        "small k; it never blocks — the bar does._",
    ]
    return "\n".join(lines) + "\n"

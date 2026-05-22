from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule

from caliper.schema.spec import EvalSpec, JudgeConfig, SandboxConfig, SkillConfig, TaskSpec

console = Console()

_BANNER = "[bold cyan]⚖  CALIPER[/bold cyan]  ·  New Evaluation Wizard"


def run_wizard(
    name: str | None = None,
    output: Path | None = None,
    skill_path: str | None = None,
    backend: str = "claude",
) -> Path:
    console.print(Panel(_BANNER, border_style="cyan", padding=(0, 2)))
    console.print()

    # ── Step 1: Skill ─────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 1 — Skill[/bold]", style="cyan"))
    skill_p = Prompt.ask(
        "  Path to skill file",
        default=skill_path or "",
        show_default=bool(skill_path),
    ).strip() or None

    backend_choice = Prompt.ask(
        "  Backend",
        choices=["claude", "codex"],
        default=backend,
    )
    model = Prompt.ask("  Model override", default="").strip() or None

    console.print()

    # ── Step 2: Judge ─────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 2 — Judge[/bold]", style="cyan"))
    judge_backend = Prompt.ask(
        "  Judge backend",
        choices=["claude", "codex"],
        default="claude",
    )
    judge_model = Prompt.ask("  Judge model override", default="").strip() or None

    console.print()

    # ── Step 3: Tasks ─────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 3 — Tasks[/bold]", style="cyan"))
    console.print("  [dim]Add tasks one by one. Leave task name empty to finish.[/dim]\n")

    tasks: list[TaskSpec] = []
    task_num = 1

    while True:
        task_name = Prompt.ask(f"  Task {task_num} name (empty to finish)", default="").strip()
        if not task_name:
            if not tasks:
                console.print("  [yellow]At least one task is required.[/yellow]")
                continue
            break

        task_id = f"task-{task_num:03d}"
        setup = Prompt.ask("  Setup command (optional)", default="").strip() or None
        cleanup = Prompt.ask("  Cleanup command (optional)", default="").strip() or None

        console.print("  Prompt [dim](enter text; finish with a blank line):[/dim]")
        prompt_lines: list[str] = []
        while True:
            line = input("    ")
            if line == "" and prompt_lines:
                break
            prompt_lines.append(line)
        prompt_text = "\n".join(prompt_lines)

        expect = Prompt.ask(
            "  Expectation [dim](what does success look like?)[/dim]",
            default="",
        ).strip() or None

        assert_val: str | None = None
        if Confirm.ask("  Add an assert script?", default=False):
            assert_choice = Prompt.ask(
                "  Assert type",
                choices=["inline", "file"],
                default="inline",
            )
            if assert_choice == "file":
                assert_val = Prompt.ask("  Path to .py file").strip()
            else:
                console.print("  Enter inline Python (blank line to finish):")
                lines: list[str] = []
                while True:
                    line = input("    ")
                    if line == "" and lines:
                        break
                    lines.append(line)
                assert_val = "\n".join(lines)

        tasks.append(
            TaskSpec.model_validate(
                {
                    "id": task_id,
                    "name": task_name,
                    "prompt": prompt_text,
                    "expect": expect,
                    "assert": assert_val,
                    "setup": setup,
                    "cleanup": cleanup,
                }
            )
        )
        console.print(f"  [green]✓ Task {task_id} added[/green]\n")
        task_num += 1

    console.print()

    # ── Step 4: Output path ───────────────────────────────────────────────────
    console.print(Rule("[bold]Step 4 — Save[/bold]", style="cyan"))
    default_name = (name or "my-eval") + ".eval.yaml"
    if output is None:
        out_str = Prompt.ask("  Save spec to", default=default_name).strip()
        output = Path(out_str)

    spec = EvalSpec(
        skill=SkillConfig(path=skill_p, backend=backend_choice, model=model),
        judge=JudgeConfig(backend=judge_backend, model=judge_model),
        sandbox=SandboxConfig(forbidden_files=[".*\\.eval\\.yaml$", "./.caliper/.*"]),
        tasks=tasks,
    )

    output.write_text(_to_yaml(spec))
    console.print()
    console.print(
        Panel(
            f"Spec written to [bold cyan]{output}[/bold cyan]\n"
            f"Validate with: [dim]caliper validate {output}[/dim]\n"
            f"Run with:      [dim]caliper run {output}[/dim]",
            title="[bold green]✓ Done[/bold green]",
            border_style="green",
        )
    )
    return output


def _to_yaml(spec: EvalSpec) -> str:
    data = spec.model_dump(exclude_none=True, by_alias=True)
    # Ensure tasks use 'assert' key (alias)
    for task in data.get("tasks", []):
        if "assert_script" in task:
            task["assert"] = task.pop("assert_script")
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

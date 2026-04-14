import logging
import re
from typing import List, Dict

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

_SECRET_KEY_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
_BATCH_COLORS = ["cyan", "green", "magenta", "yellow", "blue", "bright_cyan"]

console = Console()


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def render_config_panel(cfg) -> Panel:
    lines: List[str] = []
    c = cfg.container
    cl = cfg.cloud

    if c.image:
        lines.append(f"[bold]Image:[/bold]         {c.image}")
    if c.command:
        if isinstance(c.command, list):
            lines.append(f"[bold]Commands:[/bold]      {len(c.command)} entries")
            lines.append(f"[bold]Parallel:[/bold]      {c.execution.parallel}")
            total_batches = 1 if c.execution.parallel <= 0 else (len(c.command) + c.execution.parallel - 1) // c.execution.parallel
            for idx, cmd in enumerate(c.command, start=1):
                batch_idx = 1 if c.execution.parallel <= 0 else ((idx - 1) // c.execution.parallel) + 1
                color = _BATCH_COLORS[(batch_idx - 1) % len(_BATCH_COLORS)]
                lines.append(
                    f"  [{color}]{batch_idx}/{total_batches}: {escape(cmd)}[/{color}]"
                )
        else:
            lines.append(f"[bold]Command:[/bold]       {c.command}")
    if cl.instance_type:
        lines.append(f"[bold]Instance:[/bold]      {cl.instance_type}")
    if cl.region:
        lines.append(f"[bold]Region:[/bold]        {cl.region}")
    if cl.n_workers and cl.n_workers > 1:
        lines.append(f"[bold]Workers:[/bold]        {cl.n_workers}")
    if c.work_dir:
        lines.append(f"[bold]Work dir:[/bold]      {c.work_dir}")
    if c.run_from_copy:
        lines.append("[bold]Run from copy:[/bold] True")
    if cl.description:
        lines.append(f"[bold]Description:[/bold]   {cl.description}")
    if c.environment:
        lines.append("[bold]Environment:[/bold]")
        for k, v in c.environment.items():
            sv = str(v)
            if _SECRET_KEY_RE.search(k):
                display_v = sv[:4] + "****" if len(sv) > 4 else "****"
            elif len(sv) > 40:
                display_v = sv[:37] + "..."
            else:
                display_v = sv
            lines.append(f"  {k} = {display_v}")

    body = "\n".join(lines) if lines else "[dim]No configuration details[/dim]"
    return Panel(body, title="[bold cyan]Job Configuration[/bold cyan]", border_style="cyan")



def confirm_submission() -> bool:
    return Confirm.ask("[bold yellow]Submit this job?[/bold yellow]")


def prompt_text(label: str, default: str = None) -> str:
    """Prompt for a text value with optional default."""
    return Prompt.ask(f"  [bold]{label}[/bold]", default=default) or ""


def prompt_select(label: str, choices: List[str], custom_label: str = "Custom...") -> str:
    """Arrow-key selection menu with an option to type a custom value."""
    from simple_term_menu import TerminalMenu

    console.print(f"  [bold]{label}[/bold]")
    options = choices + [custom_label]
    menu = TerminalMenu(
        options,
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("fg_cyan", "bold"),
    )
    idx = menu.show()
    if idx is None:
        # User pressed Escape
        return choices[0]
    if idx == len(choices):
        # Custom option selected
        return prompt_text(label)
    return choices[idx]


def prompt_env_vars() -> Dict[str, str]:
    """Prompt for KEY=VALUE environment variables until empty input."""
    console.print("  [bold]Environment variables[/bold] (KEY=VALUE, empty line to finish):")
    env = {}
    while True:
        line = Prompt.ask("   ", default="")
        if not line:
            break
        if "=" not in line:
            console.print("    [dim]Skipping invalid entry (use KEY=VALUE format)[/dim]")
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


STATUS_STYLES = {
    "Running": "bold green",
    "Completed": "dim",
    "Failed": "bold red",
    "Pending": "bold yellow",
}


def _format_created_at(value) -> str:
    if not value:
        return ""
    from datetime import datetime
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value)
        else:
            dt = datetime.fromisoformat(str(value))
        return dt.strftime("%b %d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(value)


def render_jobs_table(jobs: List[Dict]) -> Table:
    table = Table(title="Jobs", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Job Name", style="cyan")
    table.add_column("Created", style="dim")
    table.add_column("Status")

    for job in jobs:
        status = job.get("status", "")
        style = STATUS_STYLES.get(status, "")
        status_text = Text(status, style=style)
        table.add_row(
            str(job["index"]),
            str(job["job_name"]),
            _format_created_at(job.get("created_at", "")),
            status_text,
        )

    return table


def interactive_job_select(jobs: List[Dict], action: str) -> str:
    from simple_term_menu import TerminalMenu

    console.print(f"\n  [bold]Select a job to {action}:[/bold]")
    options = []
    for job in jobs:
        status = job.get("status", "")
        name = job.get("job_name", "")
        created = _format_created_at(job.get("created_at", ""))
        options.append(f"{name}  ({status})  {created}")

    menu = TerminalMenu(
        options,
        cursor_index=len(options) - 1,
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("fg_cyan", "bold"),
    )
    idx = menu.show()
    if idx is None:
        raise SystemExit(0)
    return jobs[idx]["job_name"]


def print_success(msg: str) -> None:
    console.print(f"[bold green]{msg}[/bold green]")


def print_error(msg: str) -> None:
    console.print(f"[bold red]{msg}[/bold red]")


def print_warning(msg: str) -> None:
    console.print(f"[bold yellow]{msg}[/bold yellow]")


def render_job_status(job_name: str, status: str) -> None:
    """Print a single job's status with color styling."""
    style = STATUS_STYLES.get(status, "")
    status_text = Text(status, style=style)
    console.print(Text.assemble(job_name, "  ", status_text))

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import typer
import yaml
from rich.prompt import Confirm

from cryri import __version__
from cryri.api import ApiError
from cryri.config import CryConfig, DEFAULT_REGION
from cryri.display import (
    console,
    setup_logging,
    render_config_panel,
    confirm_submission,
    render_jobs_table,
    render_job_status,
    interactive_job_select,
    print_success,
    print_error,
    prompt_select,
)
from cryri.job_manager import JobManager, JobNotFoundError, ClientLibMissingError

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


def _version_callback(value: bool):
    if value:
        console.print(f"cryri [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def callback(
    version: bool = typer.Option(
        False, "--version", "-v",
        help="Show cryri version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
):
    """[bold cyan]cryri[/bold cyan] — Cloud job runner CLI."""
    setup_logging()


def _resolve_job_interactive(jm: JobManager, hash: Optional[str], action: str) -> str:
    """Resolve a job name from a hash or via interactive selection."""
    if hash is not None:
        full_name = jm.find_job_by_hash(hash)
        if full_name is None:
            raise JobNotFoundError(f"No job found matching '{hash}'")
        return full_name

    try:
        with console.status("[bold green]Fetching jobs...[/bold green]"):
            structured = jm.get_jobs_structured()
    except (ApiError, ClientLibMissingError) as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    if not structured:
        console.print("[dim]No jobs found.[/dim]")
        raise typer.Exit()

    return interactive_job_select(structured, action)


_SINCE_RE = re.compile(r"^(\d+)([mhd])$")


def _parse_since(value: str) -> datetime:
    """Parse a --since value into a datetime cutoff.

    Accepts relative durations like '30m', '2h', '1d' or ISO date strings.
    """
    m = _SINCE_RE.match(value.strip())
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return datetime.now() - delta
    return datetime.fromisoformat(value)


@app.command()
def init(
    output: str = typer.Option("run.yaml", "--output", "-o", help="Output YAML file path."),
):
    """Interactive wizard to create a job config file."""
    console.print("\n  [bold cyan]Welcome to cryri![/bold cyan] Let's set up your job.\n")

    # Derive default description from parent_dir-current_dir
    cwd = Path.cwd()
    default_description = f"{cwd.parent.name}-{cwd.name}"

    COMMAND_CHOICES = [
        "python3 main.py",
        "bash run.sh",
    ]
    IMAGE_CHOICES = [
        "cr.ai.cloud.ru/aicloud-base-images/cuda12.1-torch2-py311:0.0.36",
    ]
    WORKDIR_CHOICES = [
        ".",
    ]
    INSTANCE_CHOICES = [
        "cpu.2C.8G",
        "a100plus.1gpu.80vG.12C.96G",
    ]
    REGION_CHOICES = [
        DEFAULT_REGION,
    ]
    DESCRIPTION_CHOICES = [
        default_description,
    ]

    command = prompt_select("Command to run", COMMAND_CHOICES)
    if not command:
        print_error("Command is required.")
        raise typer.Exit(code=1)

    image = prompt_select("Docker image", IMAGE_CHOICES)
    work_dir = prompt_select("Working directory", WORKDIR_CHOICES)
    instance_type = prompt_select("Instance type", INSTANCE_CHOICES)
    region = prompt_select("Region", REGION_CHOICES)
    description = prompt_select("Description", DESCRIPTION_CHOICES)

    # Build config dict
    container = {"command": command, "image": image, "work_dir": work_dir or ".", "run_from_copy": False}

    cloud = {
        "description": description or default_description,
        "instance_type": instance_type,
        "n_workers": 1,
    }
    if region and region != DEFAULT_REGION:
        cloud["region"] = region

    config_dict = {"container": container, "cloud": cloud}

    # Build CryConfig for display
    cfg = CryConfig(**{
        "container": {**container},
        "cloud": {**cloud, "region": region or DEFAULT_REGION},
    })

    console.print()
    console.print(render_config_panel(cfg))
    console.print()

    if Path(output).exists():
        if not Confirm.ask(f"[yellow]{output}[/yellow] already exists. Overwrite?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit()

    # Write YAML with commented environment template
    with open(output, "w", encoding="utf-8") as f:
        yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
        f.write(
            "\n"
            "# Environment variables (uncomment and edit as needed):\n"
            "#   environment:\n"
            "#     HF_TOKEN: your-token-here\n"
        )
    print_success(f"Config saved to {output}")

    # Create starter main.py if using default command and it doesn't exist
    if command == "python3 main.py" and not Path("main.py").exists():
        Path("main.py").write_text(
            'import os\n\nprint("Hello from cryri!")\nprint(f"Running on {os.uname().nodename}")\n'
        )
        print_success("Created starter main.py")


@app.command()
def status(
    hash: str = typer.Argument(..., help="Job hash (or partial match)."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
):
    """Show status of a single job."""
    jm = JobManager(region)
    try:
        with console.status("[bold green]Fetching jobs...[/bold green]"):
            job = jm.find_job_with_status(hash)
    except (ApiError, ClientLibMissingError) as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    if job is None:
        print_error(f"No job found matching '{hash}'")
        raise typer.Exit(code=1)

    render_job_status(job["job_name"], job.get("status", "unknown"))


@app.command()
def submit(
    config_file: str = typer.Argument(..., help="Path to the YAML configuration file."),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Cloud region override."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt (for CI)."),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="Override command."),
    instance: Optional[str] = typer.Option(None, "--instance", "-i", help="Override instance type."),
    env: Optional[List[str]] = typer.Option(None, "--env", "-e", help="Override env var (KEY=VALUE, repeatable)."),
    workers: Optional[int] = typer.Option(None, "--workers", "-w", help="Override number of workers."),
):
    """Submit a job from a YAML config file."""
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = CryConfig(**yaml.safe_load(f))
    except FileNotFoundError:
        print_error(f"Configuration file '{config_file}' not found.")
        raise typer.Exit(code=1)
    except yaml.YAMLError as e:
        print_error(f"Error parsing YAML file: {e}")
        raise typer.Exit(code=1)

    if region:
        cfg.cloud.region = region
    if command is not None:
        cfg.container.command = command
    if instance is not None:
        cfg.cloud.instance_type = instance
    if workers is not None:
        cfg.cloud.n_workers = workers
    if env:
        if cfg.container.environment is None:
            cfg.container.environment = {}
        for item in env:
            if "=" not in item:
                print_error(f"Invalid env format (expected KEY=VALUE): {item}")
                raise typer.Exit(code=1)
            k, v = item.split("=", 1)
            cfg.container.environment[k.strip()] = v.strip()

    console.print(render_config_panel(cfg))

    if not yes and not confirm_submission():
        print_error("Submission cancelled.")
        raise typer.Exit()

    try:
        jm = JobManager(cfg.cloud.region)
        with console.status("[bold green]Submitting job...[/bold green]"):
            status = jm.submit_run(cfg)
        print_success(f"Job submitted: {status}")
    except (ApiError, ClientLibMissingError, ValueError) as e:
        print_error(f"Failed to submit job: {e}")
        raise typer.Exit(code=1)


@app.command()
def jobs(
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of latest jobs to show (0 for all)."),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status (case-insensitive)."),
    since: Optional[str] = typer.Option(None, "--since", help="Filter by time (e.g. 30m, 2h, 1d, or ISO date)."),
):
    """List running jobs."""
    jm = JobManager(region)
    try:
        with console.status("[bold green]Fetching jobs...[/bold green]"):
            structured = jm.get_jobs_structured()
    except (ApiError, ClientLibMissingError) as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    if not structured:
        console.print("[dim]No jobs found.[/dim]")
        raise typer.Exit()

    # Apply --status filter
    if status_filter:
        sf = status_filter.lower()
        structured = [j for j in structured if sf in j.get("status", "").lower()]

    # Apply --since filter
    if since:
        try:
            cutoff = _parse_since(since)
        except ValueError:
            print_error(f"Invalid --since value: {since}  (use e.g. 30m, 2h, 1d, or ISO date)")
            raise typer.Exit(code=1)
        filtered = []
        for j in structured:
            ca = j.get("created_at", "")
            if not ca:
                continue
            try:
                if isinstance(ca, (int, float)):
                    job_dt = datetime.fromtimestamp(ca)
                else:
                    job_dt = datetime.fromisoformat(str(ca))
                if job_dt >= cutoff:
                    filtered.append(j)
            except (ValueError, TypeError, OSError):
                pass
        structured = filtered

    if not structured:
        console.print("[dim]No jobs match the given filters.[/dim]")
        raise typer.Exit()

    total = len(structured)
    if limit > 0 and total > limit:
        structured = structured[-limit:]
        console.print(f"[dim]Showing {limit} of {total} jobs. Use -n 0 to show all.[/dim]\n")

    console.print(render_jobs_table(structured))


@app.command()
def logs(
    hash: Optional[str] = typer.Argument(None, help="Job hash (interactive selection if omitted)."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
    raw: bool = typer.Option(False, "--raw", help="Show raw unfiltered logs."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow logs, reconnecting until job finishes."),
):
    """Show logs for a job."""
    jm = JobManager(region)

    if hash is not None:
        try:
            job_name = _resolve_job_interactive(jm, hash, "view logs")
        except JobNotFoundError as e:
            print_error(str(e))
            raise typer.Exit(code=1)
    else:
        # No hash — show interactive selection of running jobs only
        try:
            with console.status("[bold green]Fetching jobs...[/bold green]"):
                structured = jm.get_jobs_structured()
        except (ApiError, ClientLibMissingError) as e:
            print_error(str(e))
            raise typer.Exit(code=1)

        running = [j for j in structured if j.get("status") == "Running"]
        if not running:
            console.print("[dim]No running jobs found.[/dim]")
            raise typer.Exit()

        job_name = interactive_job_select(running, "view logs")

    try:
        if follow:
            jm.show_logs_follow(job_name, raw=raw)
        else:
            jm.show_logs(job_name, raw=raw)
    except (ApiError, ClientLibMissingError) as e:
        print_error(str(e))
        raise typer.Exit(code=1)


@app.command()
def kill(
    hash: Optional[str] = typer.Argument(None, help="Job hash (interactive selection if omitted)."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
):
    """Kill a running job."""
    jm = JobManager(region)

    if hash is not None:
        try:
            job_name = _resolve_job_interactive(jm, hash, "kill")
        except JobNotFoundError as e:
            print_error(str(e))
            raise typer.Exit(code=1)
    else:
        try:
            with console.status("[bold green]Fetching jobs...[/bold green]"):
                structured = jm.get_jobs_structured()
        except (ApiError, ClientLibMissingError) as e:
            print_error(str(e))
            raise typer.Exit(code=1)

        _FINISHED = {"Completed", "Failed"}
        killable = [j for j in structured if j.get("status") not in _FINISHED]
        if not killable:
            console.print("[dim]No active jobs found.[/dim]")
            raise typer.Exit()

        job_name = interactive_job_select(killable, "kill")

    try:
        with console.status("[bold green]Terminating job...[/bold green]"):
            jm.kill_job(job_name)
        print_success(f"Job {job_name} terminated successfully.")
    except (ApiError, ClientLibMissingError) as e:
        print_error(str(e))
        raise typer.Exit(code=1)


@app.command()
def instances(
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
):
    """Show available instance types."""
    jm = JobManager(region)
    try:
        with console.status("[bold green]Fetching instance types...[/bold green]"):
            table = jm.get_instance_types()
        console.print(table)
    except (ApiError, ClientLibMissingError) as e:
        print_error(str(e))
        raise typer.Exit(code=1)


def main():
    app()


if __name__ == '__main__':
    main()

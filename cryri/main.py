import re
import json
from enum import Enum
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import typer
import yaml
from rich.prompt import Confirm

from cryri import __version__, api
from cryri.local_logs import LocalLogStore, LogPreview
from cryri.credentials import CURRENT_CONFIG, CredentialConfigError, current_config, select_config
from cryri.statuses import is_terminal
from cryri.api import ApiError
from cryri.config import CryConfig, CloudConfig, DEFAULT_REGION
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
    prompt_text,
    prompt_select,
)
from cryri.job_manager import JobManager, JobNotFoundError, ClientLibMissingError

class LogSource(str, Enum):
    auto = "auto"
    local = "local"
    api = "api"


class ApiBackend(str, Enum):
    auto = "auto"
    gateway = "gateway"
    public = "public"


app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")


def _version_callback(value: bool):
    if value:
        console.print(f"cryri [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


@app.callback()
def callback(
    ctx: typer.Context,
    credentials_file: Optional[Path] = typer.Option(
        None, "--config", help="Personal credentials TOML file; overrides CRYRI_CONFIG."
    ),
    user_home: Optional[Path] = typer.Option(
        None, "--user-home", help="Personal root; overrides CRYRI_USER_HOME and directory detection."
    ),
    backend: Optional[ApiBackend] = typer.Option(
        None, "--backend", help="API backend: auto, gateway, or public."
    ),
    version: bool = typer.Option(
        False, "--version", "-v",
        help="Show cryri version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
):
    """[bold cyan]cryri[/bold cyan] — Cloud job runner CLI."""
    setup_logging()
    try:
        selected = select_config(credentials_file, user_home)
    except (CredentialConfigError, OSError, ValueError, RuntimeError):
        print_error("Cannot select personal Cryri configuration; check --config and --user-home.")
        raise typer.Exit(code=1)
    config_token = CURRENT_CONFIG.set(selected)
    ctx.call_on_close(lambda: CURRENT_CONFIG.reset(config_token))
    token = api.BACKEND_OVERRIDE.set(backend.value if backend else None)
    ctx.call_on_close(lambda: api.BACKEND_OVERRIDE.reset(token))


@app.command("config")
def show_personal_config():
    """Show selected personal credential path and field sources, without secrets."""
    try:
        details = current_config().describe()
        details["backend"] = api.backend_name()
        typer.echo(json.dumps(details, indent=2))
    except (CredentialConfigError, ApiError) as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)


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


def _read_job_name(jm, value):
    if jm.is_full_job_name(value):
        return value
    job = jm.find_job_by_hash(value)
    if not job:
        raise JobNotFoundError("No job found matching '{}'.".format(value))
    return job


def _redact(value):
    if isinstance(value, dict):
        return {
            k: "****" if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", k, re.I) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@app.command()
def status(
    hash: str = typer.Argument(..., help="Job hash (or partial match)."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
    json_output: bool = typer.Option(False, "--json", help="Emit status metadata as JSON."),
    history: bool = typer.Option(False, "--history", help="Include Public API v3 status history."),
):
    """Show job status and identify the reporting backend."""
    jm = JobManager(region)
    try:
        name = _read_job_name(jm, hash)
        job = jm.get_job_info(name, history=history)
    except (ApiError, ClientLibMissingError, JobNotFoundError, ValueError) as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json.dumps(_redact(job), indent=2, ensure_ascii=False))
        return
    render_job_status(job["job_name"], job["status"])
    typer.echo("Source: " + job.get("status_source", "unknown"))
    if job.get("warning"):
        typer.echo(job["warning"], err=True)
    details = job.get("platform_details", {}).get("status", {}).get("details", [])
    if details and details[-1].get("message"):
        typer.echo(details[-1]["message"])


@app.command("inspect")
def inspect_job(
    hash: str = typer.Argument(..., help="Job name or partial hash."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r"),
    history: bool = typer.Option(False, "--history", help="Include Public API v3 status history."),
):
    """Print job metadata as JSON, including its status source."""
    jm = JobManager(region)
    try:
        data = jm.get_job_info(_read_job_name(jm, hash), history=history)
        typer.echo(json.dumps(_redact(data), indent=2, ensure_ascii=False))
    except (ApiError, ClientLibMissingError, JobNotFoundError, ValueError) as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)


@app.command()
def params(
    hash: str = typer.Argument(..., help="Job name or partial hash."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r"),
):
    """Print Public API launch parameters as JSON; secret-named fields are masked."""
    try:
        data = api.get_job_params(_read_job_name(JobManager(region), hash))
        typer.echo(json.dumps(_redact(data), indent=2, ensure_ascii=False))
    except (ApiError, ClientLibMissingError, JobNotFoundError, ValueError) as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)


@app.command()
def nodes(
    hash: str = typer.Argument(..., help="Job name or partial hash."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r"),
):
    """Print Public API job node information as JSON."""
    try:
        data = api.get_job_nodes(_read_job_name(JobManager(region), hash))
        typer.echo(json.dumps(_redact(data), indent=2, ensure_ascii=False))
    except (ApiError, ClientLibMissingError, JobNotFoundError, ValueError) as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)


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

    if any(j.get("status_source") == "gateway" for j in structured):
        typer.echo("Source: gateway; reported statuses may be stale.", err=True)
    console.print(render_jobs_table(structured))


@app.command()
def logs(
    hash: Optional[str] = typer.Argument(None, help="Job hash; interactive selection if omitted."),
    region: str = typer.Option(DEFAULT_REGION, "--region", "-r", help="Cloud region."),
    raw: bool = typer.Option(False, "--raw", help="Preserve timestamps, MPI prefixes, and noise."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow new output until terminal status or Ctrl-C."),
    source: LogSource = typer.Option(LogSource.auto, "--source", help="auto prefers local files; api forces HTTP."),
    log_dir: Optional[Path] = typer.Option(None, "--log-dir", envvar="CRYRI_LOG_DIR", help="Archive root; default ~/.mlspace-logs."),
    head: int = typer.Option(40, "--head", min=0, help="First lines per local file."),
    tail: int = typer.Option(200, "--tail", min=0, help="Last lines per local file, or recent API lines."),
    max_bytes: int = typer.Option(65536, "--max-bytes", min=1, help="Total preview data budget in bytes."),
    max_files: int = typer.Option(8, "--max-files", min=1, help="Maximum files in a local preview."),
    full: bool = typer.Option(False, "--full", help="Stream all selected job logs without preview limits."),
):
    """Read archived logs first, with a bounded head/tail preview by default."""
    jm = JobManager(region)
    try:
        preview = LogPreview(head=head, tail=tail, max_bytes=max_bytes,
                             max_files=max_files, full=full)
        if hash is None:
            if source == LogSource.local:
                records = LocalLogStore(log_dir).jobs()
            else:
                try:
                    records = jm.get_jobs_structured()
                except (ApiError, ClientLibMissingError):
                    if source != LogSource.auto:
                        raise
                    records = LocalLogStore(log_dir).jobs()
            if not records:
                typer.echo("No jobs found.")
                return
            job_name = interactive_job_select(records, "view logs")
        else:
            job_name = jm.resolve_log_job(hash, source=source.value, log_dir=log_dir)
        method = jm.show_logs_follow if follow else jm.show_logs
        method(job_name, raw=raw, source=source.value, log_dir=log_dir, preview=preview)
    except (ApiError, ClientLibMissingError, JobNotFoundError, OSError, ValueError) as exc:
        print_error(str(exc))
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
        except (JobNotFoundError, ApiError, ClientLibMissingError, ValueError) as e:
            print_error(str(e))
            raise typer.Exit(code=1)
    else:
        try:
            with console.status("[bold green]Fetching jobs...[/bold green]"):
                structured = jm.get_jobs_structured()
        except (ApiError, ClientLibMissingError) as e:
            print_error(str(e))
            raise typer.Exit(code=1)

        killable = [j for j in structured if not is_terminal(j.get("status"))]
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

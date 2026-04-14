import io
import shlex
from typing import Optional, List, Dict
from contextlib import redirect_stdout

from cryri import api
from cryri.api import ApiError
from cryri.config import CryConfig
from cryri.script_builder import create_batch_script_file
from cryri.utils import create_run_copy, create_job_description


class JobNotFoundError(Exception):
    pass


class ClientLibMissingError(Exception):
    pass


def _require_client_lib():
    """Import client_lib for the legacy backend path."""
    try:
        import client_lib  # noqa: F401
        return client_lib
    except ModuleNotFoundError:
        raise ClientLibMissingError(
            "client_lib is not installed. Install it to use cloud features."
        )


class JobManager:
    def __init__(self, region: str):
        self.region = region

    # ------------------------------------------------------------------
    # Legacy helpers (only used when CRYRI_USE_CLIENT_LIB=1)
    # ------------------------------------------------------------------

    def _get_jobs_legacy(self) -> List[str]:
        client_lib = _require_client_lib()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client_lib.jobs(region=self.region)
        output = buffer.getvalue()
        buffer.close()
        return output.splitlines()

    @staticmethod
    def _parse_raw_jobs(raw_jobs: List[str]) -> List[Dict]:
        result = []
        for i, raw in enumerate(raw_jobs, start=1):
            parts = raw.split(" : ")
            if len(parts) >= 3:
                result.append({
                    "index": i,
                    "created_at": parts[0].strip(),
                    "job_name": parts[1].strip(),
                    "status": parts[2].strip(),
                })
            elif len(parts) >= 2:
                result.append({
                    "index": i,
                    "created_at": "",
                    "job_name": parts[0].strip(),
                    "status": parts[1].strip(),
                })
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_jobs_structured(self) -> List[Dict]:
        if api.use_legacy_backend():
            return self._parse_raw_jobs(self._get_jobs_legacy())

        jobs = api.list_jobs(region=self.region)
        result = []
        for i, j in enumerate(jobs, start=1):
            result.append({
                "index": i,
                "created_at": j.get("created_at", ""),
                "job_name": j.get("job_name", ""),
                "status": j.get("status", ""),
            })
        return result

    def find_job_by_hash(self, partial_hash: str) -> Optional[str]:
        """Find a job by partial hash match. Returns full job_name if found."""
        for job in self.get_jobs_structured():
            if partial_hash in job["job_name"]:
                return job["job_name"]
        return None

    def find_job_with_status(self, partial_hash: str) -> Optional[Dict]:
        """Find a job by partial hash match. Returns the full job dict (with status)."""
        for job in self.get_jobs_structured():
            if partial_hash in job["job_name"]:
                return job
        return None

    def get_instance_types(self):
        if api.use_legacy_backend():
            client_lib = _require_client_lib()
            return client_lib.get_instance_types(regions=self.region)
        return api.get_instance_types(regions=self.region)

    def submit_run(self, cfg: CryConfig) -> str:
        """Submit a job run with the given configuration."""
        if cfg.container.run_from_copy:
            if not cfg.container.cry_copy_dir:
                raise ValueError(
                    f"Copy dir is not set: {cfg.container.cry_copy_dir}"
                )
            cfg.container.work_dir = create_run_copy(cfg.container)

        job_description = create_job_description(cfg)

        if isinstance(cfg.container.command, list):
            script_name = create_batch_script_file(
                work_dir=cfg.container.work_dir,
                commands=cfg.container.command,
                parallel=cfg.container.execution.parallel,
            )
            quoted_dir = shlex.quote(cfg.container.work_dir)
            run_script = f"bash -c {shlex.quote(f'cd {quoted_dir} && bash {shlex.quote(script_name)}')}"
        else:
            quoted_dir = shlex.quote(cfg.container.work_dir)
            run_script = f"bash -c {shlex.quote(f'cd {quoted_dir} && {cfg.container.command}')}"

        if api.use_legacy_backend():
            client_lib = _require_client_lib()
            job = client_lib.Job(
                base_image=cfg.container.image,
                script=run_script,
                instance_type=cfg.cloud.instance_type,
                processes_per_worker=cfg.cloud.processes_per_worker,
                n_workers=cfg.cloud.n_workers,
                region=cfg.cloud.region,
                type='binary',
                env_variables=cfg.container.environment,
                job_desc=job_description,
            )
            return job.submit()

        result = api.submit_job(
            script=run_script,
            base_image=cfg.container.image,
            instance_type=cfg.cloud.instance_type,
            region=cfg.cloud.region,
            n_workers=cfg.cloud.n_workers,
            processes_per_worker=cfg.cloud.processes_per_worker,
            env_variables=cfg.container.environment,
            job_desc=job_description,
        )
        return result.get("job_name", str(result))

    def show_logs(self, job_name: str, raw: bool = False) -> None:
        """Stream logs for a job. Expects a resolved job_name."""
        if api.use_legacy_backend():
            client_lib = _require_client_lib()
            client_lib.logs(job_name, region=self.region)
            return

        try:
            api.stream_logs(job_name, region=self.region, raw=raw)
        except ApiError as e:
            if e.status_code == 400:
                # Check job status to give a better error message
                try:
                    status = api.get_job_status(job_name, region=self.region)
                except ApiError:
                    status = "unknown"
                raise ApiError(
                    400,
                    f"Cannot read logs for job '{job_name}' (status: {status}). "
                    f"Logs may not be available for finished jobs.",
                )
            raise

    def get_job_status(self, job_name: str) -> str:
        """Get status of a single job from the jobs list."""
        for job in self.get_jobs_structured():
            if job["job_name"] == job_name:
                return job.get("status", "unknown")
        return "unknown"

    def show_logs_follow(self, job_name: str, raw: bool = False) -> None:
        """Stream logs with auto-reconnect until job finishes."""
        api.stream_logs_follow(job_name, region=self.region, raw=raw)

    def kill_job(self, job_name: str) -> str:
        """Kill a running job. Expects a resolved job_name."""
        if api.use_legacy_backend():
            client_lib = _require_client_lib()
            client_lib.kill(job_name, region=self.region)
        else:
            api.kill_job(job_name, region=self.region)

        return job_name

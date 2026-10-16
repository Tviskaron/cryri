import io
import shlex
import re
import sys
from typing import Optional, List, Dict
from contextlib import redirect_stdout

from cryri import api
from cryri.api import ApiError
from cryri.config import CryConfig
from cryri.utils import create_run_copy, create_job_description
from cryri.local_logs import LocalLogStore, LogPreview
from cryri.statuses import normalize_status


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
            result.append(dict(j, index=i, status=normalize_status(j.get("status"))))
        return result

    def find_job_by_hash(self, partial_hash: str) -> Optional[str]:
        job = self.find_job_with_status(partial_hash)
        return job["job_name"] if job else None

    def find_job_with_status(self, partial_hash: str) -> Optional[Dict]:
        jobs = self.get_jobs_structured()
        exact = [j for j in jobs if j["job_name"] == partial_hash]
        matches = exact or [j for j in jobs if partial_hash in j["job_name"]]
        if len(matches) > 1:
            raise ValueError("Ambiguous job hash; provide a longer hash or full job name.")
        return matches[0] if matches else None

    @staticmethod
    def is_full_job_name(value):
        return bool(re.fullmatch(
            r"[A-Za-z0-9_.-]+-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value
        ))

    def resolve_log_job(self, value, source="auto", log_dir=None):
        if source != "api":
            local = LocalLogStore(log_dir).resolve(value)
            if local:
                return local
            if source == "local":
                raise JobNotFoundError("No local logs found matching '{}'.".format(value))
        if self.is_full_job_name(value):
            return value
        result = self.find_job_by_hash(value)
        if not result:
            raise JobNotFoundError("No job found matching '{}'.".format(value))
        return result

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

    def show_logs(self, job_name: str, raw: bool = False, source="auto",
                  log_dir=None, preview=None) -> None:
        preview = preview or LogPreview()
        if source != "api":
            store = LocalLogStore(log_dir)
            try:
                if store.files(job_name) or source == "local":
                    store.show(job_name, options=preview, raw=raw)
                    return
            except OSError as exc:
                if source == "local":
                    raise
                print("Local logs unavailable ({}); falling back to API.".format(exc), file=sys.stderr)

        if api.use_legacy_backend():
            _require_client_lib().logs(job_name, region=self.region)
            return
        try:
            api.stream_logs(
                job_name, region=self.region, raw=raw,
                tail=0 if preview.full else max(preview.head, preview.tail),
                max_bytes=None if preview.full else preview.max_bytes,
            )
        except ApiError as exc:
            if exc.status_code == 400:
                raise ApiError(
                    400, "Log API cannot read this job. Archived logs may still be available "
                    "with --source local --log-dir PATH."
                )
            raise

    def get_job_status(self, job_name: str) -> str:
        if api.use_legacy_backend():
            job = self.find_job_with_status(job_name)
            return normalize_status(job.get("status")) if job else "Unknown"
        return api.get_job_status(job_name, region=self.region)

    def get_job_info(self, job_name, history=False):
        if api.use_legacy_backend():
            if history:
                raise ApiError(0, "Job history requires the Public API backend.")
            return {"job_name": job_name, "status": self.get_job_status(job_name),
                    "status_source": "legacy"}
        return api.get_job_info(job_name, region=self.region, history=history)

    def show_logs_follow(self, job_name: str, raw: bool = False, source="auto",
                         log_dir=None, preview=None) -> None:
        if source != "api":
            store = LocalLogStore(log_dir)
            if store.files(job_name) or source == "local":
                store.follow(job_name, options=preview, raw=raw,
                             get_status=lambda: self.get_job_status(job_name))
                return
        if api.use_legacy_backend():
            _require_client_lib().logs(job_name, region=self.region)
            return
        api.stream_logs_follow(job_name, region=self.region, raw=raw)

    def kill_job(self, job_name: str) -> str:
        """Kill a running job. Expects a resolved job_name."""
        if api.use_legacy_backend():
            client_lib = _require_client_lib()
            client_lib.kill(job_name, region=self.region)
        else:
            api.kill_job(job_name, region=self.region)

        return job_name

"""Built-in API client for cryri — replaces client_lib dependency."""

import os
import re
import time
from typing import Optional, List, Dict

import requests
from requests.exceptions import ConnectionError, Timeout
from rich.table import Table

_MAX_RETRIES = 3
_RETRY_BACKOFF = 1  # seconds; doubles each retry
_RETRYABLE_STATUS_CODES = {502, 503, 504}

# Patterns for log cleaning
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
_MPI_PREFIX_RE = re.compile(r"^\[\d+,\d+\]<std(?:out|err)>:")
_TQDM_RE = re.compile(r"\s*\d+%\|.*\|\s*\d+/\d+\s*\[.*\]")
_NCCL_NOISE = ("NCCL INFO", "ptxas info", "bytes spill stores")


class ApiError(Exception):
    """Raised on non-200 API responses."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error {status_code}: {detail}")


def use_legacy_backend() -> bool:
    """Check if legacy client_lib backend is requested via env var."""
    return os.environ.get("CRYRI_USE_CLIENT_LIB", "").strip() == "1"


def _require_env(name: str) -> str:
    """Get a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise ApiError(0, f"Environment variable {name} is not set.")
    return value


def _get_namespace() -> str:
    """Resolve namespace from NAMESPACE env var, falling back to NB_PREFIX."""
    ns = os.environ.get("NAMESPACE")
    if ns:
        return ns
    nb_prefix = os.environ.get("NB_PREFIX")
    if not nb_prefix:
        raise ApiError(
            0,
            "Neither NAMESPACE nor NB_PREFIX environment variable is set. "
            "Set NAMESPACE to your cloud namespace.",
        )
    parts = nb_prefix.split("/")
    if len(parts) < 3:
        raise ApiError(0, f"NB_PREFIX has unexpected format: {nb_prefix}")
    ns = parts[2]
    if ns == "notebook" and len(parts) >= 4:
        return parts[3]
    return ns


def _base_url() -> str:
    return f"http://{_require_env('GWAPI_ADDR')}"


def _auth_headers() -> Dict[str, str]:
    return {
        "X-Api-Key": _require_env("GWAPI_KEY"),
        "X-Namespace": _get_namespace(),
    }


def _request(method: str, path: str, *, retries: int = _MAX_RETRIES, **kwargs) -> requests.Response:
    """Make an HTTP request with automatic retry on transient failures."""
    url = f"{_base_url()}{path}"
    kwargs.setdefault("headers", _auth_headers())
    kwargs.setdefault("timeout", 30)

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code not in _RETRYABLE_STATUS_CODES or attempt == retries:
                return resp
        except (ConnectionError, Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise ApiError(0, f"Request failed after {retries + 1} attempts: {exc}") from exc

        time.sleep(_RETRY_BACKOFF * (2 ** attempt))

    raise ApiError(0, f"Request failed after {retries + 1} attempts: {last_exc}")


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------


def submit_job(
    script: str,
    base_image: str,
    instance_type: str,
    region: str,
    *,
    job_type: str = "binary",
    n_workers: int = 1,
    processes_per_worker: int = 1,
    env_variables: Optional[Dict[str, str]] = None,
    job_desc: str = "",
    flags: Optional[Dict] = None,
) -> dict:
    """Submit a job. Returns dict with ``job_name``."""
    payload = {
        "script": script,
        "base_image": base_image,
        "instance_type": instance_type,
        "region": region,
        "type": job_type,
        "n_workers": n_workers,
        "processes_per_worker": processes_per_worker,
        "env_variables": env_variables or {},
        "job_desc": job_desc,
        "flags": flags or {},
    }
    resp = _request("POST", "/run_job", json=payload)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    return resp.json()


def list_jobs(region: Optional[str] = None) -> List[dict]:
    """List jobs. Returns list of dicts with ``created_at``, ``job_name``, ``status``."""
    payload: Dict = {}
    if region:
        payload["region"] = region
    resp = _request("POST", "/job_list", json=payload)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    jobs = resp.json()
    jobs.sort(key=lambda j: j.get("created_at", ""))
    return jobs


def _clean_log_line(line: str) -> Optional[str]:
    """Strip timestamps, MPI prefixes, and tqdm bars from a log line."""
    line = _TIMESTAMP_RE.sub("", line)
    line = _MPI_PREFIX_RE.sub("", line)
    if any(noise in line for noise in _NCCL_NOISE):
        return None
    line = _TQDM_RE.sub("", line)
    line = line.strip()
    return line or None


def get_job_status(job_name: str, region: Optional[str] = None) -> str:
    """Get job status. Returns status string like 'Completed', 'Running', etc."""
    payload = {"job_name": job_name}
    if region:
        payload["region"] = region
    resp = _request("POST", "/job_status", json=payload)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    return resp.json().get("job_status", "unknown")


def stream_logs(
    job_name: str,
    region: Optional[str] = None,
    tail: int = 0,
    verbose: bool = True,
    raw: bool = False,
) -> None:
    """Stream logs for a job directly to stdout."""
    payload: Dict = {
        "job_name": job_name,
        "tail": tail,
        "verbose": verbose,
        "region": region,
    }
    # No retry for streaming — use longer read timeout since training jobs
    # can go quiet for minutes during evaluation/checkpointing.
    resp = _request(
        "POST", "/read_logs", json=payload,
        stream=True, timeout=(10, 600), retries=0,
    )
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    try:
        prev_line = None
        for chunk in resp.iter_lines():
            if not chunk:
                continue
            line = chunk.decode("utf-8")
            if raw:
                print(line)
                continue
            cleaned = _clean_log_line(line)
            if cleaned is None or cleaned == prev_line:
                continue
            prev_line = cleaned
            print(cleaned)
    except Timeout:
        raise ApiError(0, "Log stream timed out (no data for 10 minutes). The job may have finished.")


_FINISHED_STATUSES = {"Completed", "Failed", "Error", "Killed"}
_PENDING_STATUSES = {"Pending", "unknown"}
_FOLLOW_RECONNECT_DELAY = 5  # seconds
_WAIT_POLL_INTERVAL = 5  # seconds


def stream_logs_follow(
    job_name: str,
    region: Optional[str] = None,
    raw: bool = False,
) -> None:
    """Stream logs with auto-reconnect. Stops when job reaches a terminal status."""
    first = True
    while True:
        try:
            tail = 0 if first else 100
            first = False
            stream_logs(job_name, region=region, tail=tail, raw=raw)
        except (ApiError, ConnectionError, Timeout):
            pass
        except KeyboardInterrupt:
            return

        # Check if job is still running (use list_jobs — job_status endpoint is unreliable)
        try:
            jobs = list_jobs(region=region)
            status = "unknown"
            for j in jobs:
                if j.get("job_name") == job_name:
                    status = j.get("status", "unknown")
                    break
        except ApiError:
            status = "unknown"

        if status in _FINISHED_STATUSES:
            from cryri.display import render_job_status
            render_job_status(job_name, status)
            return

        try:
            time.sleep(_FOLLOW_RECONNECT_DELAY)
        except KeyboardInterrupt:
            return


def wait_for_job_start(
    job_name: str,
    region: Optional[str] = None,
    timeout: int = 600,
) -> str:
    """Poll until job leaves Pending state. Returns the new status."""
    start = time.monotonic()
    while True:
        try:
            jobs = list_jobs(region=region)
            for j in jobs:
                if j.get("job_name") == job_name:
                    status = j.get("status", "unknown")
                    if status not in _PENDING_STATUSES:
                        return status
                    break
        except ApiError:
            pass

        if time.monotonic() - start > timeout:
            raise ApiError(0, f"Job {job_name} still pending after {timeout}s")

        try:
            time.sleep(_WAIT_POLL_INTERVAL)
        except KeyboardInterrupt:
            return "interrupted"


def kill_job(job_name: str, region: str) -> str:
    """Kill a running job. Returns status message."""
    payload = {"job_name": job_name, "region": region}
    resp = _request("POST", "/delete_job/v2", json=payload)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    return resp.text


def get_instance_types(regions: str) -> Table:
    """Fetch instance types for the given region. Returns a ``rich.Table``."""
    resp = _request("GET", "/v1/clusters")
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)

    table = Table(title="Instance Types")
    table.add_column("Cluster", style="cyan")
    table.add_column("Instance Type", style="green")
    table.add_column("GPUs", justify="right")
    table.add_column("CPUs", justify="right")
    table.add_column("Memory", justify="right")

    for cluster in resp.json():
        # Only show clusters that support training jobs (mt)
        if not cluster.get("mt", False):
            continue
        cluster_key = cluster.get("cluster_key", "")
        if regions and cluster_key != regions:
            continue
        resp2 = _request("GET", f"/v1/clusters/{cluster_key}/instance_types")
        if resp2.status_code != 200:
            continue
        instance_types = resp2.json().get("instance_types", [])
        # Filter out free tier
        instance_types = [it for it in instance_types if it.get("key", "") != "free.0gpu"]
        # Sort: cpu first, then by gpu count
        instance_types.sort(
            key=lambda x: int(x.get("key", "").startswith("a100")) * 100
            + (x.get("key", "").startswith("cpu")) * 1000
            + len(x.get("key", "")) * 10
            + int(x.get("gpu", 0)),
        )
        for it in instance_types:
            table.add_row(
                cluster_key,
                it.get("key", ""),
                str(it.get("gpu", "")),
                str(it.get("cpu", "")),
                str(it.get("memory", "")),
            )

    return table



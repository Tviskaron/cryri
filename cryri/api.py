"""Built-in API client for cryri — replaces client_lib dependency."""

import os
import re
import time
from typing import Optional, List, Dict
from functools import lru_cache
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import quote
import json

from cryri.statuses import normalize_status, is_terminal
from cryri.credentials import api_settings, CredentialConfigError

import requests
from requests.exceptions import ConnectionError, Timeout
from rich.table import Table

BACKEND_OVERRIDE = ContextVar("cryri_backend_override", default=None)

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


def _request(method: str, path: str, *, retries=None, base_url=None, **kwargs) -> requests.Response:
    """Make an HTTP request with automatic retry on transient failures."""
    url = f"{base_url or _base_url()}{path}"
    if "headers" not in kwargs:
        kwargs["headers"] = _auth_headers()
    if retries is None:
        # Never replay submissions or destructive operations after an ambiguous failure.
        retries = _MAX_RETRIES if method.upper() in {"GET", "HEAD", "OPTIONS"} else 0
    kwargs.setdefault("timeout", 30)

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code not in _RETRYABLE_STATUS_CODES or attempt == retries:
                return resp
            resp.close()
        except (ConnectionError, Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise ApiError(0, f"Request failed after {retries + 1} attempts: {exc}") from exc

        time.sleep(_RETRY_BACKOFF * (2 ** attempt))

    raise ApiError(0, f"Request failed after {retries + 1} attempts: {last_exc}")



_PUBLIC_CREDENTIALS = ("MLSPACE_CLIENT_ID", "MLSPACE_CLIENT_SECRET", "MLSPACE_WORKSPACE_ID")


def _api_settings():
    try:
        return api_settings()
    except CredentialConfigError as exc:
        raise ApiError(0, str(exc)) from exc


def backend_name():
    """Select Public API when configured; keep notebook gateway access compatible."""
    if BACKEND_OVERRIDE.get() == "gateway":
        return "gateway"
    settings = _api_settings()
    backend = (BACKEND_OVERRIDE.get() or settings.get("CRYRI_API_BACKEND") or "auto").lower()
    if backend not in {"auto", "gateway", "public"}:
        raise ApiError(0, "CRYRI_API_BACKEND must be auto, gateway, or public.")
    if backend == "gateway":
        return backend
    configured = [name for name in _PUBLIC_CREDENTIALS if settings.get(name)]
    if backend == "public" or configured:
        missing = [name for name in _PUBLIC_CREDENTIALS if not settings.get(name)]
        if not (settings.get("MLSPACE_API_KEY") or os.environ.get("GWAPI_KEY")):
            missing.append("MLSPACE_API_KEY (or GWAPI_KEY)")
        if missing:
            raise ApiError(0, "Public API configuration is incomplete: " + ", ".join(missing))
        return "public"
    return "gateway"


@lru_cache(maxsize=1)
def _configured_public_client(client_id, secret, workspace_id, api_key, origin):
    from cryri.public_api import PublicClient
    return PublicClient(client_id, secret, workspace_id, api_key, origin=origin)


def _public_client():
    if backend_name() != "public":
        raise ApiError(
            0, "This endpoint requires MLSpace Public API credentials: "
            "MLSPACE_CLIENT_ID, MLSPACE_CLIENT_SECRET, MLSPACE_WORKSPACE_ID, "
            "and MLSPACE_API_KEY (or GWAPI_KEY)."
        )
    settings = _api_settings()
    return _configured_public_client(
        *(settings[name] for name in _PUBLIC_CREDENTIALS),
        settings.get("MLSPACE_API_KEY") or os.environ["GWAPI_KEY"],
        settings.get("MLSPACE_API_URL") or "https://api.ai.cloud.ru",
    )


def _job_path(job_name):
    return "/jobs/" + quote(job_name, safe="")


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
    if backend_name() == "public":
        return _public_client().json("POST", "/jobs", json=payload, retries=0)
    resp = _request("POST", "/run_job", json=payload, retries=0)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    return resp.json()


def _created_timestamp(job):
    value = job.get("created_at")
    if value is None:
        value = job.get("created_dt")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return 0


def list_jobs(region: Optional[str] = None) -> List[dict]:
    """List jobs, preserving platform metadata and normalizing known status names."""
    if backend_name() == "public":
        jobs = []
        offset = 0
        while True:
            params = {"limit": 1000, "offset": offset}
            if region:
                params["region"] = region
            data = _public_client().json("GET", "/jobs", params=params)
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                raise ApiError(502, "Public API returned an invalid jobs list.")
            page = data["jobs"]
            jobs.extend(page)
            offset += len(page)
            if not page or offset >= data.get("count", offset):
                break
        source = "public-v2"
    else:
        payload = {"region": region} if region else {}
        resp = _request("POST", "/job_list", json=payload, retries=_MAX_RETRIES)
        with resp:
            if resp.status_code != 200:
                raise ApiError(resp.status_code, resp.text)
            jobs = resp.json()
        if not isinstance(jobs, list):
            raise ApiError(502, "Gateway returned an invalid jobs list.")
        source = "gateway"
    result = [dict(j, status=normalize_status(j.get("status")), status_source=source,
                   created_at=_created_timestamp(j)) for j in jobs]
    result.sort(key=lambda j: j.get("created_at") or 0)
    return result

def _clean_log_line(line: str) -> Optional[str]:
    """Strip timestamps, MPI prefixes, and tqdm bars from a log line."""
    line = _TIMESTAMP_RE.sub("", line)
    line = _MPI_PREFIX_RE.sub("", line)
    if any(noise in line for noise in _NCCL_NOISE):
        return None
    line = _TQDM_RE.sub("", line)
    line = line.strip()
    return line or None


def get_job_info(job_name: str, region: Optional[str] = None, history: bool = False) -> dict:
    """Get platform metadata, or an explicitly identified gateway status report."""
    if backend_name() == "public":
        data = _public_client().json("GET", _job_path(job_name))
        if not isinstance(data, dict) or data.get("status") == "job not found":
            raise ApiError(404, "Job not found: " + job_name)
        if not data.get("status"):
            raise ApiError(502, "Public API returned no job status.")
        data = dict(data, status=normalize_status(data["status"]), status_source="public-v2")
        if history:
            # A Public API uid is not inferred from a Kubernetes job-name suffix.
            uid = data.get("uid")
            if not uid:
                raise ApiError(502, "Public API returned no uid for job history.")
            detail = _public_client().json("GET", _job_path(str(uid)), version=3)
            if not isinstance(detail, dict) or not isinstance(detail.get("status"), dict) or not detail["status"].get("phase"):
                raise ApiError(502, "Public API v3 returned no job phase.")
            data["platform_details"] = detail
            data["status"] = normalize_status(detail["status"]["phase"])
            data["status_source"] = "public-v3"
        return data

    if history:
        _public_client()  # Raise the actionable configuration error.
    params = {"job": job_name}
    if region:
        params["region"] = region
    resp = _request("GET", "/job_status", params=params)
    with resp:
        if resp.status_code != 200:
            raise ApiError(resp.status_code, resp.text)
        data = resp.json()
    if not isinstance(data, dict) or not data.get("job_status"):
        raise ApiError(502, "Gateway returned no job status.")
    return {
        "job_name": job_name,
        "status": normalize_status(data["job_status"]),
        "status_source": "gateway",
        "warning": "Gateway status can be stale; it does not establish job liveness. "
                   "Configure the Public API for platform status and history.",
    }


def get_job_status(job_name: str, region: Optional[str] = None) -> str:
    return get_job_info(job_name, region=region)["status"]


def get_job_params(job_name: str) -> dict:
    return _public_client().json("GET", _job_path(job_name) + "/params")


def get_job_nodes(job_name: str) -> dict:
    return _public_client().json("GET", _job_path(job_name) + "/nodes")

def stream_logs(
    job_name: str,
    region: Optional[str] = None,
    tail: int = 0,
    verbose: bool = True,
    raw: bool = False,
    max_bytes: Optional[int] = None,
) -> None:
    """Read logs with an optional bounded preview and guaranteed response cleanup."""
    from cryri.local_logs import emit_chunks

    if backend_name() == "public":
        params = {"tail": tail, "verbose": verbose}
        if region:
            params["region"] = region
        resp = _public_client().request(
            "GET", _job_path(job_name) + "/logs", params=params,
            stream=True, timeout=(10, 30 if max_bytes is not None else 600), retries=0,
        )
    else:
        resp = _request(
            "POST", "/read_logs",
            json={"job_name": job_name, "tail": tail, "verbose": verbose, "region": region},
            stream=True, timeout=(10, 30 if max_bytes is not None else 600), retries=0,
        )
    with resp:
        if resp.status_code != 200:
            raise ApiError(resp.status_code, resp.text)
        try:
            emit_chunks(resp.iter_content(chunk_size=8192), raw=raw, max_bytes=max_bytes)
        except (ConnectionError, Timeout) as exc:
            raise ApiError(0, "Log connection interrupted; try local archived logs or --follow.") from exc


_FOLLOW_RECONNECT_DELAY = 5  # seconds


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

        try:
            status = get_job_status(job_name, region=region)
        except ApiError:
            status = "Unknown"

        if is_terminal(status):
            from cryri.display import render_job_status
            render_job_status(job_name, status)
            return

        try:
            time.sleep(_FOLLOW_RECONNECT_DELAY)
        except KeyboardInterrupt:
            return


def kill_job(job_name: str, region: str) -> str:
    """Kill a running job. Returns status message."""
    payload = {"job_name": job_name, "region": region}
    if backend_name() == "public":
        data = _public_client().json("DELETE", _job_path(job_name), params={"region": region}, retries=0)
        return json.dumps(data)
    resp = _request("POST", "/delete_job/v2", json=payload, retries=0)
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    return resp.text


def get_instance_types(regions: str) -> Table:
    """Fetch instance types for the given region. Returns a ``rich.Table``."""
    if backend_name() == "public":
        data = _public_client().json("GET", "/configs", params={"cluster_type": "MT"})
        table = Table(title="Instance Types")
        for column in ["Cluster", "Instance Type", "Resources"]:
            table.add_column(column)
        for cluster in data.get("regions", []):
            if regions and cluster.get("key") != regions:
                continue
            for instance in cluster.get("instances_types", []):
                table.add_row(cluster.get("key", ""), instance.get("key", ""),
                              json.dumps(instance.get("resource", {}), sort_keys=True))
        return table
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



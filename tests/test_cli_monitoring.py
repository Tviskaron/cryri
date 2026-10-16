import json
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from cryri import api
from cryri.main import app

JOB = "lm-mpi-job-00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for name in ["CRYRI_API_BACKEND", "CRYRI_USE_CLIENT_LIB", "MLSPACE_CLIENT_ID",
                 "MLSPACE_CLIENT_SECRET", "MLSPACE_WORKSPACE_ID", "MLSPACE_API_KEY"]:
        monkeypatch.delenv(name, raising=False)


def test_status_json_uses_single_job_endpoint_for_full_name(monkeypatch):
    info = {"job_name": JOB, "status": "Failed", "status_source": "public-v2"}
    get_info = Mock(return_value=info)
    monkeypatch.setattr(api, "get_job_info", get_info)
    monkeypatch.setattr(api, "list_jobs", Mock(side_effect=AssertionError("No list lookup needed")))
    result = CliRunner().invoke(app, ["status", JOB, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == info
    get_info.assert_called_once_with(JOB, region="SR006", history=False)


def test_gateway_warning_is_visible(monkeypatch):
    monkeypatch.setattr(api, "get_job_info", Mock(return_value={
        "job_name": JOB, "status": "Running", "status_source": "gateway",
        "warning": "Gateway status can be stale.",
    }))
    result = CliRunner().invoke(app, ["status", JOB])
    assert result.exit_code == 0, result.output
    assert "gateway" in result.output and "stale" in result.output


def test_global_backend_override_is_scoped_to_invocation(monkeypatch):
    monkeypatch.setenv("CRYRI_API_BACKEND", "gateway")
    for name in ["MLSPACE_CLIENT_ID", "MLSPACE_CLIENT_SECRET", "MLSPACE_WORKSPACE_ID",
                 "MLSPACE_API_KEY"]:
        monkeypatch.setenv(name, "configured")
    seen = []

    def info(*args, **kwargs):
        seen.append(api.backend_name())
        return {"job_name": JOB, "status": "Failed", "status_source": "public-v2"}

    monkeypatch.setattr(api, "get_job_info", info)
    result = CliRunner().invoke(app, ["--backend", "public", "status", JOB])
    assert result.exit_code == 0, result.output
    assert seen == ["public"]
    assert api.backend_name() == "gateway"


def test_params_masks_secret_named_fields(monkeypatch):
    monkeypatch.setattr(api, "get_job_params", Mock(return_value={
        "env_variables": {"HF_TOKEN": "do-not-print", "BATCH_SIZE": "2"},
        "script": "python train.py",
    }))
    result = CliRunner().invoke(app, ["params", JOB])
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["env_variables"] == {"HF_TOKEN": "****", "BATCH_SIZE": "2"}
    assert "do-not-print" not in result.output


def test_inspect_history_renders_platform_details(monkeypatch):
    get_info = Mock(return_value={
        "job_name": JOB, "status": "Failed", "status_source": "public-v3",
        "platform_details": {"status": {"phase": "Failed", "details": [
            {"type": "Status", "value": "Failed", "message": "Platform stopped the job"}]}},
    })
    monkeypatch.setattr(api, "get_job_info", get_info)
    result = CliRunner().invoke(app, ["inspect", JOB, "--history"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["platform_details"]["status"]["phase"] == "Failed"
    assert get_info.call_args.kwargs["history"] is True


def test_ambiguous_job_hash_fails_before_status_request(monkeypatch):
    monkeypatch.setattr(api, "list_jobs", Mock(return_value=[
        {"job_name": "job-abc-one", "status": "Running"},
        {"job_name": "job-abc-two", "status": "Running"},
    ]))
    monkeypatch.setattr(api, "get_job_info", Mock(side_effect=AssertionError("Ambiguous target")))
    result = CliRunner().invoke(app, ["status", "abc"])
    assert result.exit_code == 1
    assert "Ambiguous" in result.output


def test_nodes_command_uses_read_endpoint(monkeypatch):
    get_nodes = Mock(return_value={"nodes": ["gpu-node"]})
    monkeypatch.setattr(api, "get_job_nodes", get_nodes)
    result = CliRunner().invoke(app, ["nodes", JOB])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["nodes"] == ["gpu-node"]
    get_nodes.assert_called_once_with(JOB)


def test_jobs_since_uses_vendor_created_dt(monkeypatch):
    for name in ["MLSPACE_CLIENT_ID", "MLSPACE_CLIENT_SECRET", "MLSPACE_WORKSPACE_ID",
                 "MLSPACE_API_KEY"]:
        monkeypatch.setenv(name, "configured")
    now = datetime.now(timezone.utc)
    client = Mock()
    client.json.return_value = {"count": 2, "jobs": [
        {"job_name": "recent-job", "status": "failed", "created_dt": now.isoformat()},
        {"job_name": "old-job", "status": "completed",
         "created_dt": (now - timedelta(days=4)).isoformat()},
    ]}
    monkeypatch.setattr(api, "_public_client", lambda: client)
    result = CliRunner().invoke(app, ["jobs", "--since", "1d"])
    assert result.exit_code == 0, result.output
    assert "recent-job" in result.output
    assert "old-job" not in result.output
    assert "Failed" in result.output
    assert "No jobs match" not in result.output

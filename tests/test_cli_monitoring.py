import json
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from cryri import api
from cryri.main import app

JOB = "lm-mpi-job-00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("command,flags,history", [
    ("status", ["--json"], False),
    ("inspect", ["--history"], True),
])
def test_job_metadata_uses_single_job_endpoint(monkeypatch, command, flags, history):
    info = {"job_name": JOB, "status": "Failed", "status_source": "public-v2"}
    get_info = Mock(return_value=info)
    monkeypatch.setattr(api, "get_job_info", get_info)
    monkeypatch.setattr(api, "list_jobs", Mock(side_effect=AssertionError("No list lookup needed")))
    result = CliRunner().invoke(app, [command, JOB, *flags])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == info
    get_info.assert_called_once_with(JOB, region="SR006", history=history)


def test_gateway_warning_is_visible(monkeypatch):
    monkeypatch.setattr(api, "get_job_info", Mock(return_value={
        "job_name": JOB, "status": "Running", "status_source": "gateway",
        "warning": "Gateway status can be stale.",
    }))
    result = CliRunner().invoke(app, ["status", JOB])
    assert result.exit_code == 0, result.output
    assert "gateway" in result.output and "stale" in result.output


@pytest.mark.parametrize("command", ["params", "nodes"])
def test_read_commands_mask_secrets(monkeypatch, command):
    monkeypatch.setattr(api, "get_job_" + command, Mock(return_value={
        "env_variables": {"HF_TOKEN": "do-not-print", "BATCH_SIZE": "2"},
    }))
    result = CliRunner().invoke(app, [command, JOB])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["env_variables"] == {"HF_TOKEN": "****", "BATCH_SIZE": "2"}
    assert "do-not-print" not in result.output


def test_ambiguous_job_hash_fails_before_status_request(monkeypatch):
    monkeypatch.setattr(api, "list_jobs", Mock(return_value=[
        {"job_name": "job-abc-one", "status": "Running"},
        {"job_name": "job-abc-two", "status": "Running"},
    ]))
    monkeypatch.setattr(api, "get_job_info", Mock(side_effect=AssertionError("Ambiguous target")))
    result = CliRunner().invoke(app, ["status", "abc"])
    assert result.exit_code == 1
    assert "Ambiguous" in result.output


def test_jobs_since_uses_vendor_created_dt(monkeypatch):
    for name in ("MLSPACE_CLIENT_ID", "MLSPACE_CLIENT_SECRET", "MLSPACE_WORKSPACE_ID", "MLSPACE_API_KEY"):
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
    assert "recent-job" in result.output and "Failed" in result.output
    assert "old-job" not in result.output

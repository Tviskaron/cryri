# pylint: disable=redefined-outer-name,wrong-import-position

import subprocess
from unittest.mock import patch, MagicMock

import yaml
import pytest
from typer.testing import CliRunner
from cryri import api, credentials
from cryri.main import app
from cryri.config import CryConfig
from cryri.job_manager import JobManager


# Define the test configuration as a YAML string
TEST_CONFIG_YAML = """
container:
  image: "cr.ai.cloud.ru/aicloud-base-images/cuda12.1-torch2-py310:0.0.36"
  command: python -c 'print("double quotes")'
  work_dir: '.'

cloud:
  region: "SR004"
  instance_type: "a100.1gpu"
  n_workers: 1
  description: "Test escape from container. #rnd #multimodality #tarasov"
"""


@patch("cryri.api.use_legacy_backend", return_value=False)
@patch("cryri.api.submit_job")
def test_submit_run_executes_command(mock_submit_job, _mock_legacy):
    """
    Tests if submit_run correctly formats and can execute the container command.
    It mocks cryri.api.submit_job and instead runs the command locally
    using subprocess to check for basic execution errors.
    """
    mock_submit_job.return_value = {"job_name": "test_job_id_123"}

    # Load config from YAML
    config_dict = yaml.safe_load(TEST_CONFIG_YAML)
    cfg = CryConfig(**config_dict)

    # Call the function under test
    jm = JobManager(cfg.cloud.region)
    job_id = jm.submit_run(cfg)

    # Assertions about the mock
    assert job_id == "test_job_id_123"
    mock_submit_job.assert_called_once()

    # Get the arguments passed to submit_job
    _, kwargs = mock_submit_job.call_args

    # Extract the script command
    script_command = kwargs.get('script')
    assert script_command is not None
    assert script_command.startswith("bash -c 'cd ")

    # Execute the script command using subprocess
    result = subprocess.run(
        script_command,
        shell=True,
        capture_output=True,
        text=True,
        check=False
    )

    # Check subprocess results
    assert result.returncode == 0, f"Subprocess failed with stderr: {result.stderr}"
    assert result.stderr == "", f"Subprocess produced stderr: {result.stderr}"
    assert "double quotes" in result.stdout


@pytest.mark.parametrize("backend,description,inside,expected", [
    ("gateway", "training", True, "[alice] training"),
    ("gateway", "[alice] training", True, "[alice] training"),
    ("gateway", "training #alice", True, "training #alice"),
    ("gateway", "alice2 training", True, "[alice] alice2 training"),
    ("gateway", None, True, None),
    ("public", "training", True, "training"),
    ("legacy", "training", True, "[alice] training"),
    ("gateway", "training", False, "training"),
])
def test_submit_description_attribution(monkeypatch, tmp_path, backend, description, inside, expected):
    work = credentials.USERS_ROOT / "alice" / "project"
    work.mkdir(parents=True)
    monkeypatch.chdir(work if inside else tmp_path)
    # Explicit auth root overrides must not change the submitter's directory identity.
    monkeypatch.setenv("CRYRI_USER_HOME", str(credentials.USERS_ROOT / "bob"))
    monkeypatch.delenv("TEAM_NAME", raising=False)
    monkeypatch.setattr(api, "use_legacy_backend", lambda: backend == "legacy")
    monkeypatch.setattr(api, "backend_name", lambda: backend)
    submit = MagicMock(return_value={"job_name": "created-job"})
    client = MagicMock()
    client.Job.return_value.submit.return_value = "created-job"
    monkeypatch.setattr(api, "submit_job", submit)
    monkeypatch.setattr("cryri.job_manager._require_client_lib", lambda: client)
    job_work = tmp_path / "work"
    job_work.mkdir()
    job_file = tmp_path / "job.yaml"
    job_file.write_text(yaml.safe_dump({
        "container": {"image": "test", "command": "echo test", "work_dir": str(job_work)},
        "cloud": {"instance_type": "test", "description": description},
    }))
    result = CliRunner().invoke(app, ["submit", str(job_file), "--yes"])
    assert result.exit_code == 0, result.output
    sent = client.Job.call_args if backend == "legacy" else submit.call_args
    if expected is None:
        expected = "[alice] " + str(job_work).replace("/", "-")
    assert sent.kwargs["job_desc"] == expected

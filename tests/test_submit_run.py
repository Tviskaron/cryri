# pylint: disable=redefined-outer-name,wrong-import-position

import subprocess
from unittest.mock import patch, MagicMock

import yaml
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


@patch("cryri.api.use_legacy_backend", return_value=False)
@patch("cryri.api.submit_job")
def test_submit_run_batched_commands_executes_all(mock_submit_job, _mock_legacy, tmp_path):
    mock_submit_job.return_value = {"job_name": "test_job_id_456"}

    config_dict = {
        "container": {
            "image": "cr.ai.cloud.ru/aicloud-base-images/cuda12.1-torch2-py310:0.0.36",
            "command": [
                "echo first",
                "sleep 0.1 && echo second",
                "echo third",
            ],
            "execution": {"parallel": 2},
            "work_dir": str(tmp_path),
        },
        "cloud": {
            "region": "SR004",
            "instance_type": "cpu.2C.8G",
            "n_workers": 1,
            "description": "batched submit test",
        },
    }
    cfg = CryConfig(**config_dict)

    jm = JobManager(cfg.cloud.region)
    job_id = jm.submit_run(cfg)
    assert job_id == "test_job_id_456"

    _, kwargs = mock_submit_job.call_args
    script_command = kwargs.get("script")
    assert script_command is not None
    assert script_command.startswith("bash -c ")
    assert ".cryri/batch_scripts/cryri_batch_" in script_command

    result = subprocess.run(
        script_command,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"Subprocess failed with stderr: {result.stderr}"
    assert "[cryri] Starting execution" in result.stdout
    assert "first" in result.stdout
    assert "second" in result.stdout
    assert "third" in result.stdout

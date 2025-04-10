# pylint: disable=redefined-outer-name,wrong-import-position

import sys
import subprocess
from unittest.mock import MagicMock, patch

import yaml
from cryri.config import CryConfig


# Since client_lib might not be installed, mock it before importing cryri modules
mock_client_lib = MagicMock()
mock_job_instance = MagicMock()
mock_job_instance.submit.return_value = "test_job_id_123"
mock_client_lib.Job.return_value = mock_job_instance
# Inject the mock into sys.modules
sys.modules['client_lib'] = mock_client_lib

# Now it's safe to import from cryri
from cryri.main import submit_run  # noqa: E402


# Define the test configuration as a YAML string
TEST_CONFIG_YAML = """
container:
  image: "cr.ai.cloud.ru/aicloud-base-images/cuda12.1-torch2-py310:0.0.36"
  command: /workspace-SR004.nfs2/d.tarasov/envs/audio-sae/bin/python -c 'print( "double quotes" )'
  work_dir: '.'

cloud:
  region: "SR004"
  instance_type: "a100.1gpu"
  n_workers: 1
  description: "Test escape from container. #rnd #multimodality #tarasov"
"""


# Use the mock for the duration of the test function
# This ensures the test doesn't rely on the actual client_lib
@patch.dict(sys.modules, {'client_lib': mock_client_lib})
def test_submit_run_executes_command():
    """
    Tests if submit_run correctly formats and can execute the container command.
    It mocks the client_lib.Job submission and instead runs the command locally
    using subprocess to check for basic execution errors.
    """
    # Load config from YAML
    config_dict = yaml.safe_load(TEST_CONFIG_YAML)
    cfg = CryConfig(**config_dict)

    # Reset mock calls before the test run
    mock_client_lib.Job.reset_mock()
    mock_job_instance.submit.reset_mock()

    # Call the function under test
    job_id = submit_run(cfg)

    # Assertions about the mock
    assert job_id == "test_job_id_123"
    mock_client_lib.Job.assert_called_once()
    mock_job_instance.submit.assert_called_once()

    # Get the arguments passed to the Job constructor
    _, kwargs = mock_client_lib.Job.call_args

    # Extract the script command
    script_command = kwargs.get('script')
    assert script_command is not None
    assert script_command.startswith('bash -c "cd ')

    # Execute the script command using subprocess
    # We run the full 'bash -c "cd ... && command"' string
    # check=False allows us to inspect stderr even if it fails
    # shell=True is necessary because we are running a shell command string
    result = subprocess.run(
        script_command,
        shell=True,
        capture_output=True,
        text=True,
        check=False  # Don't raise exception on non-zero exit
    )

    # Check subprocess results
    # Ensure the command ran without errors
    assert result.returncode == 0, f"Subprocess failed with stderr: {result.stderr}"
    # Ensure there was no output to stderr
    assert result.stderr == "", f"Subprocess produced stderr: {result.stderr}"
    # Optionally check stdout
    assert "double quotes" in result.stdout

# Reset the mock in sys.modules after tests if necessary,
# though usually test runners isolate tests.
# del sys.modules['client_lib']

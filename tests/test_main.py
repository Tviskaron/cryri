# pylint: disable=redefined-outer-name

import pytest

from cryri.config import CryConfig, ContainerConfig, CloudConfig
from cryri.job_manager import JobManager
from cryri.utils import (
    create_job_description
)
from tests.utils.mocks import mock_path_resolution, make_is_dir_mock, mock_env_vars


@pytest.fixture
@mock_path_resolution(force_is_dir=make_is_dir_mock())
def basic_config():
    return CryConfig(
        container=ContainerConfig(
            image="test-image:latest",
            command="python script.py",
            work_dir="/test/dir",
        ),
        cloud=CloudConfig(
            region="SR006",
            instance_type="cpu.small",
            priority="medium"
        )
    )


@pytest.fixture
def job_manager():
    return JobManager(region="SR006")


def test_container_config_defaults():
    config = ContainerConfig()
    assert config.image is None
    assert config.command is None
    assert config.environment is None
    assert config.work_dir is None
    assert config.run_from_copy is False
    assert config.cry_copy_dir is None


@mock_path_resolution(cwd="/mock/fake/dir")
def test_create_job_description_basic(basic_config):
    assert basic_config.container.work_dir == "/test/dir"
    description = create_job_description(basic_config)
    assert description == "-test-dir"


@mock_path_resolution(cwd="/mock/fake/dir")
def test_create_job_description_with_team(basic_config):
    basic_config.container.environment = {"TEAM_NAME": "test-team"}
    description = create_job_description(basic_config)
    assert description == "-test-dir #test-team"


@mock_env_vars(
    HOME="/mock/fake_user", MY_HOME="~/sub_user",
    WANDB_API_KEY="8aead3118j2ej28e2jee",
)
@mock_path_resolution(cwd="/mock/fake_dir/", force_is_dir=make_is_dir_mock())
def test_container_config_expand_resolve_fields_validators():
    config = ContainerConfig(
        image="test-image:latest",
        command="python script.py",
        environment={
            "HF_HOME": "$HOME/.cache/huggingface",
            "WANDB_API_KEY": "$WANDB_API_KEY",
            "WANDB_PROJECT": "LoRa-TinyLlama",
            "TEAM_NAME": "look/like/path",
        },
        work_dir=".",
        run_from_copy=True,
        cry_copy_dir="$MY_HOME/.cryri",
    )
    assert config.environment == {
        "HF_HOME": "/mock/fake_user/.cache/huggingface",
        "WANDB_API_KEY": "8aead3118j2ej28e2jee",
        "WANDB_PROJECT": "LoRa-TinyLlama",
        "TEAM_NAME": "look/like/path",
    }
    assert config.work_dir == "/mock/fake_dir"
    assert config.cry_copy_dir == "/mock/fake_user/sub_user/.cryri"

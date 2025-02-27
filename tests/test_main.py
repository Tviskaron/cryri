# pylint: disable=redefined-outer-name

import pytest
from cryri.config import CryConfig, ContainerConfig, CloudConfig
from cryri.job_manager import JobManager
from cryri.utils import create_job_description


@pytest.fixture
def basic_config():
    return CryConfig(
        container=ContainerConfig(
            image="test-image:latest",
            command="python script.py",
            work_dir="/test/dir"
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


def test_create_job_description_basic(basic_config):
    description = create_job_description(basic_config)
    assert description == "-test-dir"


def test_create_job_description_with_team(basic_config):
    basic_config.container.environment = {"TEAM_NAME": "test-team"}
    description = create_job_description(basic_config)
    assert description == "test-team--test-dir"


def test_create_job_description_with_tags(basic_config):
    basic_config.cloud.tags = ["tag1", "tag2"]
    description = create_job_description(basic_config)
    assert description == "-test-dir #tag1 #tag2"

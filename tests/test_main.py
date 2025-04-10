# pylint: disable=redefined-outer-name

import pytest
from cryri.config import CryConfig, ContainerConfig, CloudConfig
from cryri.job_manager import JobManager
from cryri.utils import create_job_description, expand_environment_vars_and_user


@pytest.fixture
def basic_config():
    return CryConfig(
        container=ContainerConfig(
            image="test-image:latest",
            command="python script.py",
            work_dir="/test/dir",
            environment={
                "TEST_VAR": "no expansion",
                "TEST_VAR2": "$EXISTING_VAR",
                "TEST_VAR3": "~/prefix/$EXISTING_VAR/suffix",
                "TEST_VAR4": "~/prefix/$NON_EXISTING_VAR/suffix",
                "TEST_VAR5": "$100 bucks",
            }
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
    assert description == "-test-dir #test-team"

def test_expand_vars_user(basic_config):
    from os import environ
    environ['EXISTING_VAR'] = '!SPECIAL_VALUE!'

    # sets both UNIX and WINDOWS user home vars
    environ['HOME'] = '<SOME_PATH>'
    environ['USERPROFILE'] = '<SOME_PATH>'

    env = basic_config.container.environment
    env = expand_environment_vars_and_user(env)
    assert env['TEST_VAR'] == 'no expansion'
    assert env['TEST_VAR2'] == '!SPECIAL_VALUE!'
    assert env['TEST_VAR3'] == '<SOME_PATH>/prefix/!SPECIAL_VALUE!/suffix'
    assert env['TEST_VAR4'] == '<SOME_PATH>/prefix/$NON_EXISTING_VAR/suffix'
    assert env['TEST_VAR5'] == '$100 bucks'

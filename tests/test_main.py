# pylint: disable=redefined-outer-name

import pytest

from cryri.config import CryConfig, ContainerConfig, CloudConfig
from cryri.job_manager import JobManager
from cryri.utils import (
    create_job_description, expand_vars_and_user
)


@pytest.fixture
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
def expandable_struct():
    return {
        1: 2,
        2: "$EXISTING_VAR",
        331: None,
        "TEST_VAR": "no expansion",
        "TEST_VAR2": "$EXISTING_VAR",
        "TEST_VAR3": "~/prefix/$EXISTING_VAR/suffix",
        "TEST_VAR4": "~/prefix/$NON_EXISTING_VAR/suffix",
        "TEST_VAR5": "$100 bucks",
        "sub_dict": {
            "TEST_VAR": "no expansion",
            "TEST_VAR2": "$EXISTING_VAR",
            "TEST_VAR3": "~/prefix/$EXISTING_VAR/suffix",
            "TEST_VAR4": "~/prefix/$NON_EXISTING_VAR/suffix",
        },
        "sub_list": [
            None,
            "TEST_VAR",
            "$EXISTING_VAR",
            "~/prefix:$EXISTING_VAR/suffix",
            "~/prefix:$NON_EXISTING_VAR/suffix",
        ],
        "sub_tuple": (
            133,
            None,
            "TEST_VAR",
            "$100 bucks",
            "$EXISTING_VAR",
            "~ prefix $EXISTING_VAR suffix",
            "~prefix $EXISTING_VAR1 suffix",
        ),
    }


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


def test_expand_vars_and_user(expandable_struct):
    # ====> Preparation
    from os import environ
    environ['EXISTING_VAR'] = '!SPECIAL_VALUE!'

    # sets both UNIX and WINDOWS user home vars
    environ['HOME'] = '<SOME_PATH>'
    environ['USERPROFILE'] = '<SOME_PATH>'
    # <====

    _expandable_struct = expand_vars_and_user(expandable_struct)
    assert _expandable_struct is not expandable_struct
    expandable_struct = _expandable_struct

    assert expandable_struct == {
        1: 2,
        2: "!SPECIAL_VALUE!",
        331: None,
        "TEST_VAR": "no expansion",
        "TEST_VAR2": "!SPECIAL_VALUE!",
        "TEST_VAR3": "<SOME_PATH>/prefix/!SPECIAL_VALUE!/suffix",
        "TEST_VAR4": "<SOME_PATH>/prefix/$NON_EXISTING_VAR/suffix",
        "TEST_VAR5": "$100 bucks",
        "sub_dict": {
            "TEST_VAR": "no expansion",
            "TEST_VAR2": "!SPECIAL_VALUE!",
            "TEST_VAR3": "<SOME_PATH>/prefix/!SPECIAL_VALUE!/suffix",
            "TEST_VAR4": "<SOME_PATH>/prefix/$NON_EXISTING_VAR/suffix",
        },
        "sub_list": [
            None,
            "TEST_VAR",
            "!SPECIAL_VALUE!",
            "<SOME_PATH>/prefix:!SPECIAL_VALUE!/suffix",
            "<SOME_PATH>/prefix:$NON_EXISTING_VAR/suffix",
        ],
        "sub_tuple": (
            133,
            None,
            "TEST_VAR",
            "$100 bucks",
            "!SPECIAL_VALUE!",
            "~ prefix !SPECIAL_VALUE! suffix",
            "~prefix $EXISTING_VAR1 suffix",
        ),
    }

    assert None is expand_vars_and_user(None)

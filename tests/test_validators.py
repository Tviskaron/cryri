# pylint: disable=redefined-outer-name
import pytest

from cryri.validators import expand_vars_and_user, sanitize_dir_path
from tests.utils.mocks import mock_env_vars, mock_path_resolution, make_is_dir_mock


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
            "prefix/~:$NON_EXISTING_VAR/suffix",
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


@mock_env_vars(
    HOME="<SOME_PATH>", EXISTING_VAR="!SPECIAL_VALUE!",
    __exclude__=["NON_EXISTING_VAR"]
)
def test_expand_vars_and_user(expandable_struct):
    result = expand_vars_and_user(expandable_struct)

    # Check #1: a copy is returned (since it is subject for expansion)
    assert result is not expandable_struct

    # Check #2: correct expansion
    assert result == {
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
            "prefix/~:$NON_EXISTING_VAR/suffix",
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

    # Check #3: gracefully support None input
    assert expand_vars_and_user(None) is None


@mock_env_vars(__exclude__=["NON_EXISTING_VAR"])
def test_expand_vars_and_user_warning(caplog):
    # Check: warns user if any "$" signs in a resulting strings
    with caplog.at_level("WARNING"):
        _ = expand_vars_and_user("$NON_EXISTING_VAR/$100 bucks")

    # There should be any warnings (from previous calls and the last one)
    assert caplog.records

    # Take last record, use .message for the actual string content
    actual_msg = caplog.records[-1].message
    expected_msg = (
        'After env vars expansion, the value still contains a `$`: '
        '"$NON_EXISTING_VAR/$100 bucks".\n'
        'Note: This might be a false alarm — just ensuring a potential silent issue '
        'does not go unnoticed.'
    )

    assert actual_msg == expected_msg


def test_sanitize_dir_path_none():
    assert sanitize_dir_path(None) is None


@mock_path_resolution(cwd="/mock/dir", force_is_dir=False)
def test_sanitize_dir_path_non_existing_dir():
    with pytest.raises(AssertionError, match="does not exist"):
        sanitize_dir_path("non_existing_dir")
    with pytest.raises(AssertionError, match="does not exist"):
        sanitize_dir_path("/path/to/non_existing_dir")


@mock_path_resolution(cwd="/mock/dir", force_is_dir=make_is_dir_mock())
def test_sanitize_dir_path_is_not_dir():
    with pytest.raises(AssertionError, match="not a directory"):
        sanitize_dir_path("file.txt")
    with pytest.raises(AssertionError, match="not a directory"):
        sanitize_dir_path("/path/to/config.yaml")


@mock_path_resolution(cwd="/mock/dir", force_is_dir=make_is_dir_mock())
def test_sanitize_dir_path_resolve():
    assert sanitize_dir_path(".") == "/mock/dir"
    assert sanitize_dir_path("/") == "/"
    assert sanitize_dir_path("./.config") == "/mock/dir/.config"
    assert sanitize_dir_path("./.config") == "/mock/dir/.config"

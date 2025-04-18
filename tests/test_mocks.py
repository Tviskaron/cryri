import os
from pathlib import Path

from tests.utils.mocks import mock_env_vars, mock_path_resolution, make_is_dir_mock


@mock_env_vars(HOME="/mock/fake_user", MY_VAR="something")
def test_mocking_env():
    assert os.environ["HOME"] == "/mock/fake_user"
    assert os.environ["USERPROFILE"] == "/mock/fake_user"
    assert os.environ["MY_VAR"] == "something"


def test_mocking_env_exclude():
    @mock_env_vars(SOME_VAR="some value")
    def with_var():
        assert os.environ["SOME_VAR"] == "some value"

        @mock_env_vars(__exclude__=["SOME_VAR"])
        def no_var():
            assert "SOME_VAR" not in os.environ
        no_var()

    with_var()


@mock_env_vars(HOME="/mock/fake_user")
@mock_path_resolution(
    cwd="/mock/fake/dir", extra_resolve_map={
        "config.yaml": "/mock/faky/fake/dir/config.yaml"
    }
)
def test_mocking_path_expanduser():
    def expanduser(path):
        return str(Path(path).expanduser())

    assert expanduser('/') == "/"
    assert expanduser("~/.app") == "/mock/fake_user/.app"
    assert expanduser(".") == "."
    assert expanduser("config.yaml") == "config.yaml"
    assert expanduser("./path/config.yaml") == "path/config.yaml"


@mock_env_vars(HOME="/mock/fake_user")
@mock_path_resolution(
    cwd="/mock/fake/dir", extra_resolve_map={
        "config.yaml": "/mock/faky/fake/dir/config.yaml"
    }
)
def test_mocking_path_resolve():
    def resolve(path):
        return str(Path(path).resolve())

    assert resolve('/') == "/"
    assert resolve("~/.app") == "/mock/fake/dir/~/.app"
    assert resolve(".") == "/mock/fake/dir"
    assert resolve("config.yaml") == "/mock/faky/fake/dir/config.yaml"
    assert resolve("./path/config.yaml") == "/mock/fake/dir/path/config.yaml"


@mock_env_vars(HOME="/mock/fake_user")
@mock_path_resolution(cwd="/mock/fake/dir")
def test_mocking_path_expanduser_resolve():
    def expanduser_resolve(path):
        return str(Path(path).expanduser().resolve())

    assert expanduser_resolve("~/.app") == "/mock/fake_user/.app"


@mock_path_resolution(force_is_dir=False)
def test_mocking_is_dir_false():
    assert not Path(".").is_dir()
    assert not Path("any/path").is_dir()


@mock_path_resolution(cwd="/mock/fake/dir", force_is_dir=True)
def test_mocking_is_dir_true():
    assert Path(".").is_dir()
    assert Path("any/path").is_dir()


@mock_path_resolution(force_is_dir=make_is_dir_mock())
def test_mocking_is_dir_dir_like():
    assert Path(".").is_dir()
    assert Path("dir/like/path").is_dir()
    assert Path("/dir/like/path/").is_dir()


@mock_path_resolution(force_is_dir=make_is_dir_mock())
def test_mocking_is_dir_file_like():
    assert Path("path/.config").is_dir()
    assert not Path("some/known/ext.txt").is_dir()
    assert not Path("some/known/ext/config.yaml").is_dir()
    assert not Path("xxxx.md").is_dir()
    assert Path(".yaml").is_dir()
    assert Path("path/file.unknown_ext").is_dir()


@mock_path_resolution(force_is_dir=make_is_dir_mock({".test1"}))
def test_mocking_is_dir_file_like_custom_ext():
    assert not Path("xx.test1").is_dir()
    assert not Path("/path/to/xxx.test1").is_dir()
    assert Path(".test1").is_dir()
    assert Path("some/known/ext.txt").is_dir()
    assert Path("config.yaml").is_dir()

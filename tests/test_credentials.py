import json
import os
from unittest.mock import Mock

import pytest
import requests
from typer.testing import CliRunner

from cryri import api, credentials
from cryri.config import CryConfig
from cryri.credentials import CredentialConfigError, select_config
from cryri.job_manager import JobManager
from cryri.main import app


@pytest.fixture
def shared(monkeypatch):
    root = credentials.USERS_ROOT
    for name in ("alice", "bob"):
        (root / name / "project").mkdir(parents=True)
    monkeypatch.chdir(root / "alice" / "project")
    return root


def save(root, name="alice", body=None):
    path = root / name / ".config" / "cryri" / "credentials.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body is not None else (
        f'[mlspace]\nclient_id = "{name}-id"\nclient_secret = "{name}-secret"\n'
        f'workspace_id = "{name}-workspace"\napi_key = "{name}-api-key"\n'
    ))
    return path


def test_personal_selection_does_not_leak_between_directories(shared, monkeypatch):
    save(shared, "bob")
    assert api.backend_name() == "gateway"  # Never search another person's files.
    assert select_config().path == save(shared)
    assert credentials.api_settings()["MLSPACE_CLIENT_ID"] == "alice-id"
    monkeypatch.chdir(shared / "bob" / "project")
    assert credentials.api_settings()["MLSPACE_CLIENT_ID"] == "bob-id"


@pytest.mark.parametrize("location", ["root", "outside", "prefix", "hidden"])
def test_no_automatic_shared_home_or_xdg_credentials(shared, monkeypatch, tmp_path, location):
    path = shared / ".config" / "cryri" / "credentials.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[mlspace]\nclient_id = "must-not-use"\n')
    monkeypatch.setenv("HOME", str(shared))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(shared / ".config"))
    places = {"root": shared, "outside": tmp_path,
              "prefix": shared.with_name("users-other"), "hidden": shared / ".local"}
    places[location].mkdir(exist_ok=True)
    monkeypatch.chdir(places[location])
    assert select_config().path is None and api.backend_name() == "gateway"


def test_optional_and_required_files(shared, monkeypatch):
    path = select_config().path
    assert api.backend_name() == "gateway" and not path.exists()
    save(shared, body="[mlspace]\n")
    assert api.backend_name() == "gateway"
    monkeypatch.setenv("CRYRI_CONFIG", str(path.with_name("missing.toml")))
    with pytest.raises(api.ApiError, match="does not exist"):
        api.backend_name()


def test_config_and_root_overrides_are_scoped_to_invocation(shared, monkeypatch, tmp_path):
    alice, bob = save(shared), save(shared, "bob")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRYRI_USER_HOME", str(shared / "alice"))
    assert select_config().path == alice
    result = CliRunner().invoke(app, ["--user-home", str(shared / "bob"), "config"])
    assert json.loads(result.stdout)["credentials_file"] == str(bob)
    monkeypatch.setenv("CRYRI_CONFIG", str(bob))
    result = CliRunner().invoke(app, ["--config", str(alice), "--backend", "gateway", "config"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["credentials_file"] == str(alice)
    assert json.loads(result.stdout)["backend"] == "gateway"
    assert credentials.CURRENT_CONFIG.get() is None and api.BACKEND_OVERRIDE.get() is None
    assert select_config().path == bob and api.backend_name() == "public"


@pytest.mark.parametrize("selector,relative", [
    ("config_file", ".config/cryri/credentials.toml"),
    ("config_file", ".cryri/credentials.toml"),
    ("config_file", "credentials.toml"),
    ("personal_root", ""),
])
def test_shared_root_credentials_are_rejected(shared, selector, relative):
    with pytest.raises(CredentialConfigError, match="personal directory"):
        select_config(**{selector: shared / relative})


def test_environment_precedence_and_redacted_diagnostics(shared, monkeypatch):
    save(shared)
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "PRIVATE-ENV-ID")
    before = dict(os.environ)
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    details = json.loads(result.stdout)
    assert details["backend"] == "public"
    assert details["settings"]["MLSPACE_CLIENT_ID"]["source"] == "environment"
    assert details["settings"]["MLSPACE_CLIENT_SECRET"]["source"] == "file"
    assert credentials.api_settings()["MLSPACE_CLIENT_ID"] == "PRIVATE-ENV-ID"
    assert dict(os.environ) == before
    for secret in ("alice-id", "alice-secret", "alice-api-key", "PRIVATE-ENV-ID"):
        assert secret not in result.output
    monkeypatch.setenv("MLSPACE_CLIENT_SECRET", "")
    with pytest.raises(api.ApiError, match="MLSPACE_CLIENT_SECRET"):
        api.backend_name()


def test_backend_precedence_and_incomplete_credentials(shared, monkeypatch):
    save(shared, body='[mlspace]\nclient_id = "partial"\n')
    with pytest.raises(api.ApiError, match="MLSPACE_CLIENT_SECRET"):
        api.backend_name()
    save(shared, body='[cryri]\nbackend = "gateway"\n')
    assert api.backend_name() == "gateway"
    monkeypatch.setenv("CRYRI_API_BACKEND", "public")
    with pytest.raises(api.ApiError, match="MLSPACE_CLIENT_ID"):
        api.backend_name()
    result = CliRunner().invoke(app, ["--backend", "gateway", "config"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("body", [
    '[mlspace]\nclient_secret = "PRIVATE-SECRET"\nbad = [',
    '[mlspace]\nclient_secret = ["PRIVATE-SECRET"]\n',
    '[mlspace]\nunknown = "PRIVATE-SECRET"\n',
], ids=["syntax", "type", "unknown-field"])
def test_config_errors_do_not_echo_secrets(shared, body):
    save(shared, body=body)
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 1
    assert "PRIVATE-SECRET" not in result.output and "Traceback" not in result.output


@pytest.mark.parametrize("directory", [False, True])
def test_automatic_config_cannot_follow_symlinks_to_another_user(shared, directory):
    bob = save(shared, "bob")
    path = shared / "alice" / ".config"
    if directory:
        path.symlink_to(bob.parent.parent, target_is_directory=True)
    else:
        (path / "cryri").mkdir(parents=True)
        (path / "cryri" / "credentials.toml").symlink_to(bob)
    with pytest.raises(api.ApiError, match="selected personal directory"):
        api.backend_name()


def test_local_logs_and_help_do_not_require_valid_credentials(shared):
    save(shared, body="broken TOML [PRIVATE-SECRET")
    archive = shared / "archive" / "test-job" / "retry.000" / "rank.0"
    archive.mkdir(parents=True)
    (archive / "stdout").write_text("local output\n")
    result = CliRunner().invoke(app, ["logs", "test-job", "--source", "local",
                                     "--log-dir", str(shared / "archive")])
    assert result.exit_code == 0 and "local output" in result.output
    for args in (["--help"], ["submit", "--help"], ["--version"]):
        assert CliRunner().invoke(app, args).exit_code == 0


def test_file_auth_preserves_description_and_stays_out_of_job_environment(shared, monkeypatch):
    save(shared)

    def response(data):
        result = requests.Response()
        result.status_code = 200
        result._content = json.dumps(data).encode()
        result._content_consumed = True
        return result

    request = Mock(side_effect=[
        response({"token": {"access_token": "test-access-token"}}),
        response({"job_name": "created-test-job"}),
    ])
    monkeypatch.setattr(api, "_request", request)
    monkeypatch.delenv("TEAM_NAME", raising=False)
    before = dict(os.environ)
    cfg = CryConfig(container={"work_dir": str(shared / "bob" / "project"), "image": "test", "command": "echo test"},
                    cloud={"description": "training", "instance_type": "test"})
    assert JobManager("SR006").submit_run(cfg) == "created-test-job"
    auth, submit = request.call_args_list
    assert auth.kwargs["json"] == {"client_id": "alice-id", "client_secret": "alice-secret"}
    assert submit.kwargs["headers"]["x-workspace-id"] == "alice-workspace"
    assert submit.kwargs["headers"]["x-api-key"] == "alice-api-key"
    assert submit.kwargs["json"]["job_desc"] == "training"
    assert submit.kwargs["json"]["env_variables"] == {} and submit.kwargs["retries"] == 0
    assert dict(os.environ) == before

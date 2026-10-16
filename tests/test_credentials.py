import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from typer.testing import CliRunner

from cryri import api, credentials
from cryri.credentials import CredentialConfigError, select_config
from cryri.main import app


@pytest.fixture
def shared(monkeypatch, tmp_path):
    for name in credentials.SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CRYRI_USE_CLIENT_LIB", raising=False)
    root = credentials.USERS_ROOT
    for name in ("alice", "bob"):
        (root / name / "project").mkdir(parents=True)
    monkeypatch.chdir(root / "alice" / "project")
    monkeypatch.setenv("GWAPI_KEY", "gateway-test-key")
    api._configured_public_client.cache_clear()
    return root


def save(root, name="alice", extra=""):
    path = root / name / ".config" / "cryri" / "credentials.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[mlspace]\nclient_id = "' + name + '-id"\n'
        'client_secret = "' + name + '-secret"\n'
        'workspace_id = "' + name + '-workspace"\n'
        'api_key = "' + name + '-api-key"\n' + extra
    )
    return path


def test_infer_personal_config_from_working_directory(shared):
    path = save(shared)
    chosen = select_config()
    assert chosen.path == path
    assert chosen.personal_root == shared / "alice"
    assert chosen.source == "working-directory"
    assert chosen.effective_values()["MLSPACE_CLIENT_ID"] == "alice-id"
    assert api.backend_name() == "public"


@pytest.mark.parametrize("location", ["shared", "outside", "prefix", "hidden"])
def test_never_use_shared_home_or_xdg_config(shared, monkeypatch, tmp_path, location):
    path = shared / ".config" / "cryri" / "credentials.toml"
    path.parent.mkdir(parents=True)
    path.write_text('[mlspace]\nclient_id = "must-not-use"\n')
    monkeypatch.setenv("HOME", str(shared))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(shared / ".config"))
    places = {
        "shared": shared,
        "outside": tmp_path,
        "prefix": shared.with_name("users-other"),
        "hidden": shared / ".local" / "bin",
    }
    places[location].mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(places[location])
    assert select_config().path is None
    assert api.backend_name() == "gateway"


def test_no_personal_file_preserves_gateway_and_does_not_create_it(shared):
    path = select_config().path
    assert not path.exists()
    assert api.backend_name() == "gateway"
    assert not path.exists()


def test_empty_file_preserves_gateway(shared):
    path = save(shared)
    path.write_text("# Authentication is optional\n[mlspace]\n")
    assert api.backend_name() == "gateway"


def test_does_not_search_another_persons_directory(shared):
    save(shared, "bob")
    assert api.backend_name() == "gateway"


def test_cwd_changes_select_new_identity_for_library_calls(shared, monkeypatch):
    save(shared, "alice")
    save(shared, "bob")
    assert credentials.api_settings()["MLSPACE_CLIENT_ID"] == "alice-id"
    monkeypatch.chdir(shared / "bob" / "project")
    assert credentials.api_settings()["MLSPACE_CLIENT_ID"] == "bob-id"


def test_personal_root_override_from_outside(shared, monkeypatch, tmp_path):
    path = save(shared)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRYRI_USER_HOME", str(shared / "alice"))
    assert select_config().path == path
    result = CliRunner().invoke(app, ["--user-home", str(shared / "bob"), "config"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.stdout)
    assert info["personal_root"] == str(shared / "bob")
    assert not info["file_exists"]
    assert info["backend"] == "gateway"


def test_shared_root_cannot_be_selected_as_personal_root(shared):
    with pytest.raises(CredentialConfigError, match="personal directory"):
        select_config(personal_root=shared)


@pytest.mark.parametrize("relative", [".config/cryri/credentials.toml", ".cryri/credentials.toml", "credentials.toml"])
def test_explicit_config_cannot_select_shared_root_credentials(shared, relative):
    with pytest.raises(CredentialConfigError, match="personal directory"):
        select_config(config_file=shared / relative)


def test_personal_root_override_cannot_select_hidden_shared_directory(shared):
    with pytest.raises(CredentialConfigError, match="personal directory"):
        select_config(personal_root=shared / ".config")


def test_explicit_config_overrides_environment_file_and_personal_root(shared, monkeypatch):
    alice = save(shared)
    bob = save(shared, "bob")
    monkeypatch.setenv("CRYRI_CONFIG", str(bob))
    assert select_config().path == bob
    chosen = select_config(alice, shared / "bob")
    assert chosen.path == alice
    assert chosen.effective_values()["MLSPACE_CLIENT_ID"] == "alice-id"


def test_explicit_file_is_required_but_personal_root_file_is_optional(shared, monkeypatch):
    missing = shared / "alice" / "missing.toml"
    monkeypatch.setenv("CRYRI_CONFIG", str(missing))
    with pytest.raises(api.ApiError, match="does not exist"):
        api.backend_name()
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_environment_overrides_file_without_mutating_environment(shared, monkeypatch):
    save(shared)
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "environment-id")
    before = dict(os.environ)
    values = credentials.api_settings()
    assert values["MLSPACE_CLIENT_ID"] == "environment-id"
    assert values["MLSPACE_CLIENT_SECRET"] == "alice-secret"
    assert dict(os.environ) == before
    monkeypatch.setenv("MLSPACE_CLIENT_SECRET", "")
    with pytest.raises(api.ApiError, match="MLSPACE_CLIENT_SECRET"):
        api.backend_name()


def test_file_backend_and_cli_precedence(shared, monkeypatch):
    save(shared, extra='\n[cryri]\nbackend = "gateway"\n')
    assert api.backend_name() == "gateway"
    monkeypatch.setenv("CRYRI_API_BACKEND", "public")
    assert api.backend_name() == "public"
    result = CliRunner().invoke(app, ["--backend", "gateway", "config"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["backend"] == "gateway"


def test_incomplete_public_credentials_are_actionable(shared):
    path = save(shared)
    path.write_text('[mlspace]\nclient_id = "incomplete"\n')
    with pytest.raises(api.ApiError, match="MLSPACE_CLIENT_SECRET"):
        api.backend_name()
    result = CliRunner().invoke(app, ["--backend", "gateway", "config"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("body", [
    '[mlspace]\nclient_secret = "PRIVATE-SECRET"\nbad = [',
    '[mlspace]\nclient_secret = ["PRIVATE-SECRET"]\n',
    '[mlspace]\nunexpected = "PRIVATE-SECRET"\n',
    '[unknown]\nsecret = "PRIVATE-SECRET"\n',
    '[cryri]\nbackend = "PRIVATE-SECRET"\n',
])
def test_config_errors_never_echo_secret_values(shared, body):
    path = save(shared)
    path.write_text(body)
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 1
    assert "PRIVATE-SECRET" not in result.output
    assert "Traceback" not in result.output


def test_rejects_oversize_config(shared):
    path = save(shared)
    path.write_bytes(b"#" * 65537)
    with pytest.raises(api.ApiError, match="64 KiB"):
        api.backend_name()


@pytest.mark.parametrize("symlink_directory", [False, True])
def test_automatic_credentials_cannot_escape_personal_root(shared, symlink_directory):
    bob = save(shared, "bob")
    alice_config = shared / "alice" / ".config"
    if symlink_directory:
        alice_config.symlink_to(bob.parent.parent, target_is_directory=True)
    else:
        target = alice_config / "cryri" / "credentials.toml"
        target.parent.mkdir(parents=True)
        target.symlink_to(bob)
    with pytest.raises(api.ApiError, match="selected personal directory"):
        api.backend_name()


def test_config_command_masks_all_values_and_reports_sources(shared, monkeypatch):
    path = save(shared)
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "PRIVATE-ENV-ID")
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    details = json.loads(result.stdout)
    assert details["credentials_file"] == str(path)
    assert details["backend"] == "public"
    assert details["settings"]["MLSPACE_CLIENT_ID"] == {
        "configured": True, "source": "environment"
    }
    assert details["settings"]["MLSPACE_CLIENT_SECRET"]["source"] == "file"
    for secret in ("alice-id", "alice-secret", "alice-api-key", "PRIVATE-ENV-ID"):
        assert secret not in result.output


def test_cli_configuration_is_scoped_and_refreshes_between_invocations(shared):
    alice = save(shared)
    bob = save(shared, "bob")
    seen = []

    def show(*args, **kwargs):
        seen.append(credentials.api_settings()["MLSPACE_CLIENT_ID"])
        return {"job_name": "test", "status": "Completed", "status_source": "public-v2"}

    from unittest.mock import patch
    with patch.object(api, "get_job_info", side_effect=show):
        for path in (alice, bob):
            result = CliRunner().invoke(app, ["--config", str(path), "status",
                "lm-mpi-job-00000000-0000-4000-8000-000000000001"])
            assert result.exit_code == 0, result.output
    assert seen == ["alice-id", "bob-id"]
    assert credentials.CURRENT_CONFIG.get() is None
    assert credentials.api_settings()["MLSPACE_CLIENT_ID"] == "alice-id"


def test_local_logs_and_help_do_not_require_valid_credentials(shared):
    path = save(shared)
    path.write_text("broken TOML [PRIVATE-SECRET")
    archive = shared / "archive" / "test-job" / "retry.000" / "rank.0"
    archive.mkdir(parents=True)
    (archive / "stdout").write_text("local output\n")
    result = CliRunner().invoke(app, ["logs", "test-job", "--source", "local",
                                     "--log-dir", str(shared / "archive")])
    assert result.exit_code == 0, result.output
    assert "local output" in result.output
    for args in (["--help"], ["submit", "--help"], ["--version"]):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
        assert "PRIVATE-SECRET" not in result.output


def response(data):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(data).encode()
    result._content_consumed = True
    return result


def test_submit_uses_file_auth_without_injecting_credentials_into_job(shared, monkeypatch):
    save(shared)
    request = Mock(side_effect=[
        response({"token": {"access_token": "test-access-token"}}),
        response({"job_name": "created-test-job"}),
    ])
    monkeypatch.setattr(api, "_request", request)
    before = dict(os.environ)
    result = api.submit_job(script="echo test", base_image="test-image",
                            instance_type="test-instance", region="SR006")
    assert result["job_name"] == "created-test-job"
    auth, submit = request.call_args_list
    assert auth.args == ("POST", "/service_auth")
    assert auth.kwargs["json"] == {"client_id": "alice-id", "client_secret": "alice-secret"}
    assert submit.kwargs["headers"]["x-workspace-id"] == "alice-workspace"
    assert submit.kwargs["headers"]["x-api-key"] == "alice-api-key"
    assert submit.kwargs["json"]["env_variables"] == {}
    assert submit.kwargs["retries"] == 0
    assert dict(os.environ) == before

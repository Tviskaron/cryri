import json
from unittest.mock import Mock

import pytest
import requests

from cryri import api
from cryri.api import ApiError


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result._content_consumed = True
    return result


def public_environment(monkeypatch):
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "client")
    monkeypatch.setenv("MLSPACE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MLSPACE_WORKSPACE_ID", "workspace-id")


@pytest.mark.parametrize("status", ["Failed", "Running"])
def test_correct_gateway_status_contract(monkeypatch, status):
    request = Mock(return_value=response({"job_status": status, "status": "ok"}))
    monkeypatch.setattr(api, "_request", request)
    info = api.get_job_info("job-name", region="SR006")
    request.assert_called_once_with(
        "GET", "/job_status", params={"job": "job-name", "region": "SR006"})
    assert info["status"] == status
    assert info["status_source"] == "gateway"
    assert "does not establish job liveness" in info["warning"]


def test_missing_status_is_not_running(monkeypatch):
    monkeypatch.setattr(api, "_request", Mock(return_value=response({"status": "ok"})))
    with pytest.raises(ApiError, match="no job status"):
        api.get_job_status("job-name")


def test_auto_backend_and_incomplete_public_configuration(monkeypatch):
    assert api.backend_name() == "gateway"
    monkeypatch.setenv("MLSPACE_CLIENT_ID", "client")
    with pytest.raises(ApiError, match="MLSPACE_CLIENT_SECRET"):
        api.backend_name()
    monkeypatch.setenv("CRYRI_API_BACKEND", "gateway")
    assert api.backend_name() == "gateway"


def test_new_endpoints_require_public_configuration():
    with pytest.raises(ApiError, match="MLSPACE_CLIENT_ID"):
        api.get_job_params("job-name")


def test_public_status_auth_and_metadata(monkeypatch):
    public_environment(monkeypatch)
    request = Mock(side_effect=[
        response({"token": {"access_token": "short-lived-token"}}),
        response({"job_name": "job-name", "status": "failed", "uid": "platform-uid",
                  "completed_at": 123, "status_updated_at": "2026-10-16T15:49:00Z"}),
    ])
    monkeypatch.setattr(api, "_request", request)
    info = api.get_job_info("job-name")
    assert info["status"] == "Failed"
    assert info["status_source"] == "public-v2"
    assert info["completed_at"] == 123
    auth = request.call_args_list[0]
    assert auth.args == ("POST", "/service_auth")
    assert auth.kwargs["retries"] == 0
    assert auth.kwargs["base_url"] == "https://api.ai.cloud.ru/public/v2"
    assert request.call_args_list[1].kwargs["headers"]["x-workspace-id"] == "workspace-id"


def test_history_uses_api_uid_not_job_name_suffix(monkeypatch):
    public_environment(monkeypatch)
    client = Mock()
    client.json.side_effect = [
        {"job_name": "job-name-123", "status": "running", "uid": "different-platform-uid"},
        {"status": {"phase": "Failed", "details": [
            {"type": "Status", "value": "Failed", "message": "Stopped by platform"}]}},
    ]
    monkeypatch.setattr(api, "_public_client", lambda: client)
    info = api.get_job_info("job-name-123", history=True)
    assert client.json.call_args_list[1].args == ("GET", "/jobs/different-platform-uid")
    assert client.json.call_args_list[1].kwargs == {"version": 3}
    assert info["status"] == "Failed"
    assert info["status_source"] == "public-v3"


def test_public_pagination_and_normalized_metadata(monkeypatch):
    public_environment(monkeypatch)
    client = Mock()
    client.json.side_effect = [
        {"count": 3, "jobs": [{"job_name": "b", "created_dt": "2026-10-16T00:00:02Z", "status": "running"},
                              {"job_name": "a", "created_dt": "2026-10-16T03:00:01+03:00", "status": "failed"}]},
        {"count": 3, "jobs": [{"job_name": "c", "created_dt": "2026-10-16T00:00:03Z", "status": "stopped"}]},
    ]
    monkeypatch.setattr(api, "_public_client", lambda: client)
    jobs = api.list_jobs("SR006")
    assert [j["job_name"] for j in jobs] == ["a", "b", "c"]
    assert jobs[0]["status"] == "Failed"
    assert isinstance(jobs[0]["created_at"], (int, float))
    assert jobs[0]["created_dt"] == "2026-10-16T03:00:01+03:00"
    assert jobs[2]["status"] == "Stopped"
    assert client.json.call_args_list[1].kwargs["params"]["offset"] == 2


def test_not_found_public_payload_is_not_success(monkeypatch):
    public_environment(monkeypatch)
    client = Mock()
    client.json.return_value = {"job_name": "absent", "status": "job not found"}
    monkeypatch.setattr(api, "_public_client", lambda: client)
    with pytest.raises(ApiError) as error:
        api.get_job_info("absent")
    assert error.value.status_code == 404


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_mutations_are_never_retried_on_ambiguous_timeout(monkeypatch, method):
    request = Mock(side_effect=requests.Timeout("timeout"))
    monkeypatch.setattr(api.requests, "request", request)
    monkeypatch.setattr(api.time, "sleep", Mock())
    with pytest.raises(ApiError, match="1 attempts"):
        api._request(method, "/jobs")
    assert request.call_count == 1


def test_read_retries_transient_status(monkeypatch):
    request = Mock(side_effect=[response({}, 503), response({"ok": True})])
    monkeypatch.setattr(api.requests, "request", request)
    monkeypatch.setattr(api.time, "sleep", Mock())
    result = api._request("GET", "/job_status")
    assert result.status_code == 200
    assert request.call_count == 2


def test_public_refreshes_token_once_on_401(monkeypatch):
    public_environment(monkeypatch)
    request = Mock(side_effect=[
        response({"token": {"access_token": "old-token"}}),
        response({"error": "expired"}, 401),
        response({"token": {"access_token": "new-token"}}),
        response({"job_name": "job", "status": "completed"}),
    ])
    monkeypatch.setattr(api, "_request", request)
    assert api.get_job_status("job") == "Completed"
    assert request.call_count == 4
    assert request.call_args.kwargs["headers"]["authorization"] == "new-token"


def test_auth_error_does_not_print_credentials(monkeypatch):
    public_environment(monkeypatch)
    monkeypatch.setattr(api, "_request", Mock(
        return_value=response({"error": "client-secret workspace-key"}, 401)))
    with pytest.raises(ApiError) as error:
        api.get_job_status("job")
    assert "client-secret" not in str(error.value)
    assert "workspace-key" not in str(error.value)


def test_public_resource_error_masks_secrets(monkeypatch):
    public_environment(monkeypatch)
    monkeypatch.setattr(api, "_request", Mock(side_effect=[
        response({"token": {"access_token": "short-lived-token"}}),
        response({"error": "client-secret workspace-key short-lived-token"}, 500),
    ]))
    with pytest.raises(ApiError) as error:
        api.get_job_status("job")
    assert all(secret not in str(error.value)
               for secret in ["client-secret", "workspace-key", "short-lived-token"])


def test_public_rejects_cleartext_origin(monkeypatch):
    public_environment(monkeypatch)
    monkeypatch.setenv("MLSPACE_API_URL", "http://api.invalid")
    with pytest.raises(ApiError, match="HTTPS"):
        api.get_job_info("job")


def test_read_endpoints_and_path_escaping(monkeypatch):
    public_environment(monkeypatch)
    client = Mock()
    monkeypatch.setattr(api, "_public_client", lambda: client)
    api.get_job_params("job/name")
    assert client.json.call_args.args == ("GET", "/jobs/job%2Fname/params")
    api.get_job_nodes("job/name")
    assert client.json.call_args.args == ("GET", "/jobs/job%2Fname/nodes")


def test_public_submission_and_delete_contracts(monkeypatch):
    public_environment(monkeypatch)
    client = Mock()
    client.json.return_value = {"job_name": "new-job"}
    monkeypatch.setattr(api, "_public_client", lambda: client)
    assert api.submit_job("echo ok", "image", "cpu.2C.8G", "SR006")["job_name"] == "new-job"
    assert client.json.call_args.args == ("POST", "/jobs")
    assert client.json.call_args.kwargs["retries"] == 0
    api.kill_job("job-name", "SR006")
    assert client.json.call_args.args == ("DELETE", "/jobs/job-name")
    assert client.json.call_args.kwargs["params"] == {"region": "SR006"}
    assert client.json.call_args.kwargs["retries"] == 0


def test_api_preview_closes_response_and_caps_long_lines(monkeypatch, capsys):
    payload = b"x" * (1024 * 1024)
    result = response({})
    result._content = payload
    closed = Mock()
    result.close = closed
    monkeypatch.setattr(api, "_request", Mock(return_value=result))
    api.stream_logs("job-name", raw=True, max_bytes=100)
    output = capsys.readouterr().out
    assert output.count("x") == 100
    assert "preview byte limit" in output
    closed.assert_called_once()


@pytest.mark.parametrize("status", ["stopped", "Terminated"])
def test_log_follower_stops_for_normalized_terminal_status(monkeypatch, status):
    monkeypatch.setattr(api, "stream_logs", Mock())
    monkeypatch.setattr(api, "get_job_status", Mock(return_value=status))
    sleep = Mock(side_effect=AssertionError("Follower must stop"))
    monkeypatch.setattr(api.time, "sleep", sleep)
    api.stream_logs_follow("job-name")
    sleep.assert_not_called()

"""Isolate tests from credentials and services on the shared notebook."""
import pytest

from cryri import api, credentials


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for name in (*credentials.SETTING_NAMES, "CRYRI_CONFIG", "CRYRI_USER_HOME", "CRYRI_USE_CLIENT_LIB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "USERS_ROOT", tmp_path / "users")
    monkeypatch.setenv("GWAPI_ADDR", "gateway.invalid")
    monkeypatch.setenv("GWAPI_KEY", "workspace-key")
    monkeypatch.setenv("NAMESPACE", "test-workspace")
    api._configured_public_client.cache_clear()

"""Keep unit tests independent of credentials on the shared notebook."""
import pytest

from cryri import credentials


@pytest.fixture(autouse=True)
def isolated_personal_config(monkeypatch, tmp_path):
    monkeypatch.delenv("CRYRI_CONFIG", raising=False)
    monkeypatch.delenv("CRYRI_USER_HOME", raising=False)
    monkeypatch.setattr(credentials, "USERS_ROOT", tmp_path / "users")

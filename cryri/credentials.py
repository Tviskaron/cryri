"""Optional personal MLSpace configuration for shared notebook accounts."""

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.8-3.10
    import tomli as tomllib


USERS_ROOT = Path("/home/jovyan")
CURRENT_CONFIG = ContextVar("cryri_personal_config", default=None)
_FIELDS = {
    "client_id": "MLSPACE_CLIENT_ID",
    "client_secret": "MLSPACE_CLIENT_SECRET",
    "workspace_id": "MLSPACE_WORKSPACE_ID",
    "api_key": "MLSPACE_API_KEY",
    "api_url": "MLSPACE_API_URL",
}
SETTING_NAMES = tuple(_FIELDS.values()) + ("CRYRI_API_BACKEND",)
_MAX_CONFIG_BYTES = 65536


class CredentialConfigError(ValueError):
    """A configuration error whose message is safe to display."""


def _absolute(value):
    return Path(value).expanduser().resolve()


def _check_personal_path(path, *, directory=False):
    try:
        relative = path.relative_to(USERS_ROOT.resolve())
    except ValueError:
        return  # Explicit portable paths outside the notebook tree are allowed.
    if (len(relative.parts) < (1 if directory else 2)
            or relative.parts[0].startswith(".")):
        raise CredentialConfigError(
            "Shared notebook credentials must be inside a personal directory under /home/jovyan."
        )


@dataclass
class PersonalConfig:
    path: Optional[Path]
    personal_root: Optional[Path]
    source: str
    required: bool = False
    _values: Optional[Dict[str, str]] = field(default=None, init=False, repr=False)

    def file_values(self):
        if self._values is not None:
            return dict(self._values)
        if self.path is None:
            self._values = {}
            return {}
        try:
            # Automatic discovery stays within the inferred personal directory,
            # including when .config or the credential file is a symlink.
            if not self.required:
                self.path.resolve().relative_to(self.personal_root)
            with self.path.open("rb") as handle:
                data = handle.read(_MAX_CONFIG_BYTES + 1)
        except FileNotFoundError:
            if self.required:
                raise CredentialConfigError(
                    "Selected Cryri credentials file does not exist: {}".format(self.path)
                ) from None
            self._values = {}
            return {}
        except (OSError, ValueError, RuntimeError):
            raise CredentialConfigError(
                "Cannot read Cryri credentials inside the selected personal directory: {}".format(self.path)
            ) from None
        if len(data) > _MAX_CONFIG_BYTES:
            raise CredentialConfigError("Cryri credentials file exceeds 64 KiB: {}".format(self.path))
        try:
            document = tomllib.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError):
            # Parser diagnostics can contain the offending line, including secrets.
            raise CredentialConfigError("Invalid TOML in Cryri credentials file: {}".format(self.path)) from None
        if set(document) - {"mlspace", "cryri"}:
            raise CredentialConfigError("Cryri credentials support only [mlspace] and [cryri] tables.")
        mlspace = document.get("mlspace", {})
        cryri = document.get("cryri", {})
        if (not isinstance(mlspace, dict) or not isinstance(cryri, dict)
                or set(mlspace) - set(_FIELDS) or set(cryri) - {"backend"}):
            raise CredentialConfigError("Unsupported field in Cryri [mlspace] or [cryri] configuration.")
        values = {_FIELDS[key]: value for key, value in mlspace.items()}
        if "backend" in cryri:
            values["CRYRI_API_BACKEND"] = cryri["backend"]
        if any(not isinstance(value, str) for value in values.values()):
            raise CredentialConfigError("Cryri credential and backend values must be TOML strings.")
        if values.get("CRYRI_API_BACKEND", "auto") not in {"auto", "gateway", "public"}:
            raise CredentialConfigError("Cryri backend must be auto, gateway, or public.")
        self._values = values
        return dict(values)

    def effective_values(self):
        values = self.file_values()
        # Do not export credentials into the process or submitted job environment.
        for name in SETTING_NAMES:
            if name in os.environ:
                values[name] = os.environ[name]
        return values

    def describe(self):
        values = self.effective_values()
        file_values = self.file_values()
        return {
            "credentials_file": str(self.path) if self.path else None,
            "personal_root": str(self.personal_root) if self.personal_root else None,
            "selection": self.source,
            "file_exists": self.path.is_file() if self.path else False,
            "settings": {
                name: {
                    "configured": bool(values.get(name)),
                    "source": "environment" if name in os.environ else
                              "file" if name in file_values else "unset",
                }
                for name in SETTING_NAMES
            },
        }


def select_config(config_file=None, personal_root=None, *, cwd=None):
    """Choose one personal file; never fall back to the shared home config."""
    explicit = config_file if config_file is not None else os.environ.get("CRYRI_CONFIG")
    if explicit:
        path = _absolute(explicit)
        _check_personal_path(path)
        return PersonalConfig(path, None, "explicit", required=True)

    chosen_root = personal_root if personal_root is not None else os.environ.get("CRYRI_USER_HOME")
    if chosen_root:
        root = _absolute(chosen_root)
        _check_personal_path(root, directory=True)
        source = "personal-root"
    else:
        current = _absolute(cwd if cwd is not None else Path.cwd())
        shared = USERS_ROOT.resolve()
        try:
            relative = current.relative_to(shared)
        except ValueError:
            return PersonalConfig(None, None, "none")
        if not relative.parts or relative.parts[0].startswith("."):
            return PersonalConfig(None, None, "none")
        root = shared / relative.parts[0]
        source = "working-directory"
    return PersonalConfig(root / ".config" / "cryri" / "credentials.toml", root, source)


def current_config():
    """CLI invocations get an isolated lazy snapshot; library calls rediscover."""
    return CURRENT_CONFIG.get() or select_config()


def api_settings():
    return current_config().effective_values()

"""Shared status names for the gateway and Public API."""

_STATUS_NAMES = (
    "Registered", "Validating", "Pending", "Running", "Completing", "Completed",
    "Succeeded", "Failed", "Error", "Killed", "Deleted", "Stopped", "Terminated",
    "Terminating", "Restarting", "Cancelled", "Unknown",
)
_NAMES = {name.lower(): name for name in _STATUS_NAMES}
TERMINAL_STATUSES = frozenset({
    "Completed", "Succeeded", "Failed", "Error", "Killed", "Deleted",
    "Stopped", "Terminated", "Cancelled",
})


def normalize_status(value):
    if not isinstance(value, str) or not value.strip():
        return "Unknown"
    value = value.strip()
    return _NAMES.get(value.lower(), value)


def is_terminal(value):
    return normalize_status(value) in TERMINAL_STATUSES

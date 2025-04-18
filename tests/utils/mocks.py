import os
import tempfile
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from unittest.mock import patch


def mock_env_vars(__exclude__=None, **env_vars):
    """
    Decorator to patch environment variables.
    Usage: @mock_env_vars(
            HOME="/fake/home", MY_VAR="value", __exclude__=["TO_BE_NON_EXISTING_VAR"]
        )
    """
    def decorator(test_func):
        @wraps(test_func)
        def wrapper(*args, **kwargs):
            with patch.dict(os.environ, env_vars, clear=False):
                # Delete excluded keys from os.environ temporarily
                to_restore = {k: os.environ[k] for k in __exclude__ if k in os.environ}
                for k in to_restore:
                    os.environ.pop(k)
                try:
                    return test_func(*args, **kwargs)
                finally:
                    os.environ.update(to_restore)
        return wrapper

    # set of excluded keys
    __exclude__ = set(__exclude__ or [])

    # automatically add 'USERPROFILE' if 'HOME' is present to cover WINDOWS case too
    if 'HOME' in env_vars and 'USERPROFILE' not in env_vars:
        env_vars['USERPROFILE'] = env_vars['HOME']

    return decorator


def make_is_dir_mock(file_extensions=None):
    """Construct a mock for `Path.is_dir()` method based on a file extension presence."""
    def _mock(self: Path) -> bool:
        return self.suffix not in file_extensions

    if isinstance(file_extensions, str):
        file_extensions = {file_extensions}
    file_extensions = file_extensions or {".py", ".yaml", ".json", ".env", ".txt", ".md"}
    return _mock


def mock_path_resolution(
        cwd=None, extra_resolve_map=None, force_is_dir=make_is_dir_mock
):
    """
    Decorator to mock Path.resolve(), cwd, and optionally Path.is_dir().

    Args:
        cwd (str): mocked current working directory. Defaults to temp dir.
        extra_resolve_map (dict): maps input path strings to resolved output strings.
        force_is_dir (bool | callable): if True or False, patches Path.is_dir().
            If callable(Path) -> bool, uses custom logic per path.
    """
    def decorator(test_func):
        @wraps(test_func)
        def wrapper(*args, **kwargs):
            # Fake Path.resolve() logic
            def custom_resolve(self: Path):
                key = str(self)
                if extra_resolve_map and key in extra_resolve_map:
                    return Path(extra_resolve_map[key])
                return Path(os.path.join(self.cwd(), key))

            # Fake Path.is_dir() logic (optional)
            def is_dir_wrapper(self: Path):
                if callable(force_is_dir):
                    return force_is_dir(self)
                return bool(force_is_dir)

            with tempfile.TemporaryDirectory() as temp_dir:
                # use provided cwd or create temp dir (which is auto-deleted)
                target_cwd = Path(cwd or temp_dir)

                # fancy dynamic way of stacking `with` context managers
                with ExitStack() as stack:
                    stack.enter_context(patch("os.getcwd", return_value=str(target_cwd)))
                    stack.enter_context(patch("pathlib.Path.cwd", return_value=target_cwd))
                    stack.enter_context(patch("pathlib.Path.resolve", new=custom_resolve))

                    if force_is_dir is not None:
                        stack.enter_context(
                            patch("pathlib.Path.is_dir", new=is_dir_wrapper)
                        )

                    return test_func(*args, **kwargs)

        return wrapper
    return decorator

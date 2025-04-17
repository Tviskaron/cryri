import hashlib
import logging
import os
import shutil
from datetime import datetime
from os.path import expanduser, expandvars
from pathlib import Path
from typing import Any, Union, List, Dict, Tuple, Optional

from cryri.config import CryConfig, ContainerConfig

DATETIME_FORMAT = "%Y_%m_%d_%H%M"
HASH_LENGTH = 6


def create_job_description(cfg: CryConfig) -> str:
    job_description = cfg.cloud.description
    if job_description is None:
        job_description = cfg.container.work_dir
        for prefix in ['/home/jovyan']:
            if job_description.startswith(prefix):
                job_description = job_description[len(prefix):]
                break
        job_description = job_description.replace('/', '-')

    team_name = None
    if cfg.container.environment is not None:
        team_name = cfg.container.environment.get("TEAM_NAME", None)
    if team_name is None:
        team_name = os.environ.get('TEAM_NAME', None)

    if team_name is not None:
        job_description = f"{job_description} #{team_name}"

    return job_description


def create_run_copy(cfg: ContainerConfig) -> str:
    """Create a copy of the work directory for the run."""
    now = datetime.now()
    now_str = now.strftime(DATETIME_FORMAT)
    hash_suffix = hashlib.sha1(
        now.strftime(f"{DATETIME_FORMAT}%S").encode()
    ).hexdigest()[:HASH_LENGTH]

    run_copy_dir = str(
        Path(cfg.cry_copy_dir) / f"run_{now_str}_{hash_suffix}"
    )

    ignore_func = shutil.ignore_patterns(*cfg.exclude_from_copy)
    shutil.copytree(
        src=cfg.work_dir,
        dst=run_copy_dir,
        ignore=ignore_func
    )

    return run_copy_dir


def expand_config_vars_and_user(cfg: ContainerConfig):
    cfg.environment = expand_vars_and_user(cfg.environment)
    cfg.work_dir = expand_vars_and_user(cfg.work_dir)
    cfg.cry_copy_dir = expand_vars_and_user(cfg.cry_copy_dir)


def sanitize_config_paths(cfg: ContainerConfig):
    cfg.work_dir = sanitize_dir_path(cfg.work_dir)
    cfg.cry_copy_dir = sanitize_dir_path(cfg.cry_copy_dir)


def expand_vars_and_user(
        s: Union[None, str, Tuple[Any], List[Any], Dict[Any, Any]]
) -> Union[None, str, Tuple[Any], List[Any], Dict[Any, Any]]:
    """
    Universal function that returns a copy of an input with values expanded,
    if they are str and contain known expandable parts (`~` home or `$XXX` env var).

    Original object is returned iff it is not a subject for expansion. So, it's better
    read as "this is not an in-place/mutating operation!"
    """

    if s is None:
        return None

    if isinstance(s, tuple):
        # noinspection PyTypeChecker
        return tuple(expand_vars_and_user(x) for x in s)

    if isinstance(s, list):
        return [expand_vars_and_user(x) for x in s]

    if isinstance(s, dict):
        return {k: expand_vars_and_user(v) for k, v in s.items()}

    if not isinstance(s, str):
        return s

    # NB: expand vars then user, since vars could be expanded into a path
    #   that requires user expansion
    # NB: expect only known/existing environment vars to be expanded!
    #   others will be left as-is
    s = expanduser(expandvars(s))

    # notify user if any $'s in the string to catch cases when env vars
    # are expected to be present while they are not (e.g. rc-file sourcing failed)
    if "$" in s:
        logging.warning(
            'After env vars expansion, the value still contains a `$`:\n'
            '"%s"\n'
            'Note: This might be a false alarm — just ensuring a potential silent issue '
            'does not go unnoticed.',
            s
        )

    return s


def sanitize_dir_path(p: Optional[str]) -> Optional[str]:
    if p is None:
        return None

    # NB: it expects already expanded path
    # NB: it expects an existing path (= all parts along the path are existing)

    # resolve path => absolute normalized path
    p = Path(p).resolve()

    # drop non-dir last part => dir path
    if not p.is_dir():
        p = p.parent

    return str(p)

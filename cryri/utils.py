import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path

from cryri.config import CryConfig, ContainerConfig
from cryri.validators import expand_vars_and_user, sanitize_dir_path

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

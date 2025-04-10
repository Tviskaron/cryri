import os
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from cryri.config import CryConfig

DATETIME_FORMAT = "%Y_%m_%d_%H%M"
HASH_LENGTH = 6


def create_job_description(cfg: CryConfig) -> str:
    team_name = None
    if cfg.container.environment is not None:
        team_name = cfg.container.environment.get("TEAM_NAME", None)
    if team_name is None:
        team_name = os.environ.get('TEAM_NAME', None)
    job_description = cfg.cloud.description
    if job_description is None:
        job_description = str(Path(cfg.container.work_dir).resolve()).replace('/home/jovyan/', '').replace('/', '-')
    if team_name is not None:
        job_description = f"{job_description} #{team_name}"

    return job_description


def create_run_copy(cfg: CryConfig) -> Path:
    """Create a copy of the work directory for the run."""
    copy_from_folder = Path(cfg.container.work_dir).parent.resolve()
    now = datetime.now().strftime(DATETIME_FORMAT)
    hash_suffix = hashlib.sha1(datetime.now().strftime(f"{DATETIME_FORMAT}S").encode()).hexdigest()[:HASH_LENGTH]
    run_name = f"run_{now}_{hash_suffix}"
    run_folder = Path(cfg.container.cry_copy_dir) / run_name

    ignore_fun = shutil.ignore_patterns(*cfg.container.exclude_from_copy)
    shutil.copytree(
        copy_from_folder,
        run_folder,
        ignore=ignore_fun
    )

    return run_folder

def expand_environment_vars_and_user(environment: dict):
    if environment is None:
        return None

    from os.path import expandvars, expanduser

    # NB: expand vars then user, since vars could be expanded into a path
    #   that requires user expansion
    # NB2: expect only known/existing environment vars to be expanded!
    #   others will be left as-is
    return {
        k: expanduser(expandvars(v))
        for k, v in environment.items()
    }

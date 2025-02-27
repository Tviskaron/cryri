import os
import hashlib
import shutil
import logging
import argparse
from pathlib import Path

from datetime import datetime
from typing import Dict, List, Optional
from contextlib import redirect_stdout

import io
import yaml

from rich.console import Console
from pydantic import BaseModel

# Constants
DEFAULT_REGION = "SR006"
DEFAULT_PRIORITY = "medium"
DATETIME_FORMAT = "%Y_%m_%d_%H%M"
HASH_LENGTH = 6

try:
    import client_lib
except ImportError:
    logging.warning("client_lib not found. Some functionality may be limited.")


class ContainerConfig(BaseModel):
    image: str = None
    command: str = None
    environment: Dict = None
    work_dir: str = None
    run_from_copy: bool = False
    cry_copy_dir: str = None


class CloudConfig(BaseModel):
    region: str = "SR006"
    instance_type: str = None
    n_workers: int = 1
    priority: str = "medium"
    description: str = None
    tags: List[str] = []


class CryConfig(BaseModel):
    container: ContainerConfig = ContainerConfig()
    cloud: CloudConfig = CloudConfig()


class JobManager:
    def __init__(self, region: str):
        self.region = region
        self.console = Console()

    def get_jobs(self) -> List[str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client_lib.jobs(region=self.region)
        output = buffer.getvalue()
        buffer.close()
        return output.splitlines()

    def find_job_by_hash(self, partial_hash: str) -> Optional[str]:
        """Find a job by partial hash match. Returns full hash if found, None otherwise."""
        for job_name in self.get_jobs():
            job_hash = self.raw_job_to_id(job_name)
            if partial_hash in job_hash:
                return job_hash
        return None

    @staticmethod
    def raw_job_to_id(job_string: str) -> str:
        return job_string.split(" : ")[1].strip()

    def get_instance_types(self):
        return client_lib.get_instance_types(regions=self.region)

    def show_logs(self, job_hash: str) -> None:
        full_hash = self.find_job_by_hash(job_hash)
        if full_hash:
            client_lib.logs(full_hash, region=self.region)
        else:
            logging.error("No job found with hash: %s", job_hash)

    def kill_job(self, job_hash: str) -> None:
        full_hash = self.find_job_by_hash(job_hash)
        if full_hash:
            client_lib.kill(full_hash, region=self.region)
            logging.info("Job %s terminated successfully", full_hash)
        else:
            logging.error("No job found with hash: %s", job_hash)


def submit_run(cfg: CryConfig) -> str:
    """Submit a job run with the given configuration."""
    if cfg.container.run_from_copy and cfg.container.cry_copy_dir:
        cfg.container.work_dir = _create_run_copy(cfg)

    job_description = create_job_description(cfg)
    logging.info("Submitting job with description: %s", job_description)

    try:
        job = client_lib.Job(
            base_image=cfg.container.image,
            script=f'bash -c "cd {str(Path(cfg.container.work_dir).resolve())} && {cfg.container.command}"',
            instance_type=cfg.cloud.instance_type,
            n_workers=cfg.cloud.n_workers,
            region=cfg.cloud.region,
            type='binary',
            env_variables=cfg.container.environment,
            priority_class=cfg.cloud.priority,
            job_desc=job_description,
        )
        return job.submit()
    except Exception as e:
        logging.error("Failed to submit job: %s", e)
        raise


def _create_run_copy(cfg: CryConfig) -> Path:
    """Create a copy of the work directory for the run."""
    copy_from_folder = Path(cfg.container.work_dir).parent.resolve()
    now = datetime.now().strftime(DATETIME_FORMAT)
    hash_suffix = hashlib.sha1(datetime.now().strftime(f"{DATETIME_FORMAT}S").encode()).hexdigest()[:HASH_LENGTH]
    run_name = f"run_{now}_{hash_suffix}"
    run_folder = Path(cfg.container.cry_copy_dir) / run_name
    shutil.copytree(copy_from_folder, run_folder)
    return run_folder


def create_job_description(cfg: CryConfig):
    team_name = None
    if cfg.container.environment is not None:
        team_name = cfg.container.environment.get("TEAM_NAME", None)
    if team_name is None:
        team_name = os.environ.get('TEAM_NAME', None)
    job_description = cfg.cloud.description
    if job_description is None:
        job_description = str(Path(cfg.container.work_dir).resolve()).replace('/home/jovyan/', '').replace('/', '-')
    if team_name is not None:
        job_description = f"{team_name}-{job_description}"

    if cfg.cloud.tags:
        tags_with_prefix = [f"#{tag}" for tag in cfg.cloud.tags]
        job_description = f"{job_description} {' '.join(tags_with_prefix)}"

    return job_description


def _config_from_args(args):
    cloud_cfg = CloudConfig()
    if args.region is not None:
        cloud_cfg.region = args.region

    cfg = CryConfig(cloud=cloud_cfg)
    return cfg


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="Script for managing runs and logs.")

    # Default mode: Run configuration from a YAML file
    parser.add_argument(
        "config_file",
        nargs="?",
        default=None,
        help="Path to the YAML configuration file for the run. This is the default option."
    )

    # Option 1: Show logs
    parser.add_argument(
        "--logs",
        metavar="HASH",
        help="Provide the hash of the container to display its logs."
    )

    # Option 2: Show runned jobs
    parser.add_argument(
        "--jobs",
        action="store_true",
        help="Display a list of runned jobs."
    )

    # Option 3: Kill job
    parser.add_argument(
        "--kill",
        metavar="HASH",
        help="Provide the hash of the job to terminate it."
    )

    # Option 4: Get instance types
    parser.add_argument(
        "--instance_types",
        action="store_true",
        help="Display types of available instances."
    )

    parser.add_argument(
        "--region",
        metavar="SR004 / SR006",
        help="Provide cloud region."
    )

    args = parser.parse_args()
    cfg = _config_from_args(args)

    job_manager = JobManager(cfg.cloud.region)

    try:
        if args.logs:
            job_manager.show_logs(args.logs)
        elif args.instance_types:
            instance_types_table = job_manager.get_instance_types()
            job_manager.console.print(instance_types_table)
        elif args.jobs:
            for job in job_manager.get_jobs():
                print(job)
        elif args.kill:
            job_manager.kill_job(args.kill)
        elif args.config_file:
            _handle_config_file(args.config_file)
        else:
            logging.warning("No valid arguments provided. Use --help for more information.")
    except Exception as e:
        logging.error("An error occurred: %s", e)
        raise


def _handle_config_file(config_file: str) -> None:
    """Handle the configuration file processing and job submission."""
    logging.info("Running configuration from: %s", config_file)
    try:
        with open(config_file, "r", encoding="utf-8") as file:
            cfg = CryConfig(**yaml.safe_load(file))
            status = submit_run(cfg)
            logging.info("Job submitted with status: %s", status)
    except FileNotFoundError:
        logging.error("Configuration file '%s' not found.", config_file)
    except yaml.YAMLError as e:
        logging.error("Error parsing YAML file: %s", e)


if __name__ == '__main__':
    main()

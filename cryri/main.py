import os
import hashlib
import shutil
from datetime import datetime
from typing import Dict
from contextlib import redirect_stdout

from rich.console import Console

import argparse
import yaml
from pydantic import BaseModel
from pathlib import Path
import io

try:
    import client_lib
except ImportError:
    pass


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


class CryConfig(BaseModel):
    container: ContainerConfig = ContainerConfig()
    cloud: CloudConfig = CloudConfig()


def submit_run(cfg):
    if cfg.container.run_from_copy and cfg.container.cry_copy_dir:
        copy_from_folder = Path(cfg.container.work_dir).parent.resolve()

        now = datetime.now().strftime("%Y_%m_%d_%H%M")
        hash_suffix = hashlib.sha1(datetime.now().strftime("%Y%m%d_%H%M%S").encode()).hexdigest()[:6]
        run_name = f"run_{now}_{hash_suffix}"
        run_folder = Path(cfg.container.cry_copy_dir) / run_name
        shutil.copytree(copy_from_folder, run_folder)

        cfg.container.work_dir = run_folder

    job_description = create_job_description(cfg)
    print(f"Submitting job with description: {job_description}")

    mnist_tf_run = client_lib.Job(
        base_image=cfg.container.image,
        script=f'bash -c "cd {str(Path(cfg.container.work_dir).resolve())} && {cfg.container.command}"',
        instance_type=cfg.cloud.instance_type,
        n_workers=1,
        region=cfg.cloud.region,
        type='binary',
        env_variables=cfg.container.environment,
        priority_class=cfg.cloud.priority,
        job_desc=job_description,
    )

    status = mnist_tf_run.submit()

    return status

def create_job_description(cfg):
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
    return job_description

def raw_job_to_id(job_string):
    return job_string.split(" : ")[1].strip()


def get_jobs(region):
    # Redirect stdout to capture the output
    buffer = io.StringIO()

    with redirect_stdout(buffer):
        client_lib.jobs(region=region)
    # Extract captured output
    output = buffer.getvalue()
    buffer.close()

    return output.splitlines()

def get_instance_types(region):

    return client_lib.get_instance_types(regions=region)


def _config_from_args(args):
    cloud_cfg = CloudConfig()
    if args.region is not None:
        cloud_cfg.region = args.region

    cfg = CryConfig(cloud=cloud_cfg)
    return cfg

def main():
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

    if args.logs:
        cfg = _config_from_args(args)
        print(f"[region={cfg.cloud.region}]Fetching logs for container with hash: {args.logs}")
        for job_name in get_jobs(region=cfg.cloud.region):
            job_hash = raw_job_to_id(job_name)
            if args.logs in job_hash:
                client_lib.logs(job_hash, region=cfg.cloud.region)
                break
    elif args.instance_types:
        cfg = _config_from_args(args)
        print(f"[region={cfg.cloud.region}] Displaying instance types:")
        instance_types_table = get_instance_types(region=cfg.cloud.region)

        console = Console()
        console.print(instance_types_table)
    elif args.jobs:
        cfg = _config_from_args(args)
        print(f"[region={cfg.cloud.region}] Displaying runned jobs:")
        user_jobs = get_jobs(region=cfg.cloud.region)
        for job in user_jobs:
            print(job)
    elif args.kill:
        cfg = _config_from_args(args)
        print(f"[region={cfg.cloud.region}] Removing job with hash: {args.kill}")
        for job_name in get_jobs(region=cfg.cloud.region):
            job_hash = raw_job_to_id(job_name)
            if args.kill in job_hash:
                client_lib.kill(job_hash, region=cfg.cloud.region)
    elif args.config_file:
        print(f"Running configuration from: {args.config_file}")
        try:
            with open(args.config_file, "r") as file:
                cfg = CryConfig(**yaml.safe_load(file))
                status = submit_run(cfg)
                print(f"Job submitted with status: {status}")
        except FileNotFoundError:
            print(f"Error: Configuration file '{args.config_file}' not found.")
        except yaml.YAMLError as e:
            print(f"Error parsing YAML file: {e}")
    else:
        print("No valid arguments provided. Use --help for more information.")


if __name__ == '__main__':
    main()
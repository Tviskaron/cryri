import logging
import argparse
from pathlib import Path

import yaml

from cryri.config import CryConfig, CloudConfig
from cryri.job_manager import JobManager
from cryri.utils import create_job_description, create_run_copy

try:
    import client_lib
except ImportError:
    logging.warning("client_lib not found. Some functionality may be limited.")


def submit_run(cfg: CryConfig) -> str:
    """Submit a job run with the given configuration."""
    if cfg.container.run_from_copy and cfg.container.cry_copy_dir:
        cfg.container.work_dir = create_run_copy(cfg)

    job_description = create_job_description(cfg)
    logging.info("Submitting job with description: %s", job_description)

    try:
        job = client_lib.Job(
            base_image=cfg.container.image,
            script=f'bash -c "cd {str(Path(cfg.container.work_dir).resolve())} && {cfg.container.command}"',
            instance_type=cfg.cloud.instance_type,
            processes_per_worker=cfg.cloud.processes_per_worker,
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


def _config_from_args(args):
    cloud_cfg = CloudConfig()
    if args.region is not None:
        cloud_cfg.region = args.region

    cfg = CryConfig(cloud=cloud_cfg)
    return cfg


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


def get_instance_types(region):

    return client_lib.get_instance_types(regions=region)


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

    parser.add_argument('--version', action='store_true', help="Show version of cryri")

    args = parser.parse_args()
    if args.version:
        import importlib
        try:
            version = importlib.metadata.version("cryri")
            print(version)
        except importlib.metadata.PackageNotFoundError:
            print("Cryri package not found.")
        return

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


if __name__ == '__main__':
    main()

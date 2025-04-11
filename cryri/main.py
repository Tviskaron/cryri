import argparse
import importlib
import logging

import yaml

from cryri.config import CryConfig, CloudConfig
from cryri.job_manager import JobManager
from cryri.utils import (
    create_job_description, create_run_copy, expand_config_vars_and_user, sanitize_config_paths
)

try:
    import client_lib
except ModuleNotFoundError:
    logging.warning("client_lib not found. Some functionality may be limited.")


def submit_run(cfg: CryConfig) -> str:
    """Submit a job run with the given configuration."""
    expand_config_vars_and_user(cfg.container)
    sanitize_config_paths(cfg.container)

    if cfg.container.run_from_copy:
        assert cfg.container.cry_copy_dir, \
            f'Copy dir is not set: {cfg.container.cry_copy_dir}'
        cfg.container.work_dir = create_run_copy(cfg.container)

    job_description = create_job_description(cfg)
    logging.info("Submitting job with description: %s", job_description)

    try:
        quoted_command = cfg.container.command.replace('"', '\\"')
        run_script = f'bash -c "cd {cfg.container.work_dir} && {quoted_command}"'

        job = client_lib.Job(
            base_image=cfg.container.image,
            script=run_script,
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


def _setup_arg_parser():
    """Set up and return the argument parser."""
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

    return parser


def _check_version():
    """Check and print the version of cryri."""
    try:
        version = importlib.metadata.version("cryri")
        print(version)
    except importlib.metadata.PackageNotFoundError:
        print("Cryri package not found.")


def _execute_command(args, job_manager):
    """Execute the appropriate command based on the provided arguments."""
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


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    parser = _setup_arg_parser()
    args = parser.parse_args()

    if args.version:
        _check_version()
        return

    cfg = _config_from_args(args)
    job_manager = JobManager(cfg.cloud.region)

    try:
        _execute_command(args, job_manager)
    except Exception as e:
        logging.error("An error occurred: %s", e)
        raise


if __name__ == '__main__':
    main()

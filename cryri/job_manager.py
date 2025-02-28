import io
import requests
import logging
import time
from typing import List, Optional
from contextlib import redirect_stdout
from rich.console import Console

try:
    import client_lib
except ImportError:
    logging.warning("client_lib not found. Some functionality may be limited.")


class JobManager:
    def __init__(self, region: str, fetch_logs_max_retries=20, job_status_interval=30):
        self.region = region
        self.console = Console()

        self.job_status_interval = job_status_interval
        self.fetch_logs_max_retries = fetch_logs_max_retries

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

    def get_job_status(self, job_hash: str) -> dict:
        """Fetch the job status from the API."""
        r = requests.get(
            f"http://{client_lib.environment.GW_API_ADDR}/job_status",
            params={"job": job_hash, "region": self.region},
            headers={"X-Api-Key": client_lib.environment.GW_API_KEY, "X-Namespace": client_lib.environment.NAMESPACE},
        )
        return r.json()

    def show_logs(self, job_hash: str) -> None:
        n_retries = 0
        while True:
            job_status = self.get_job_status(job_hash)['job_status']
            if job_status in ['Pending', 'Inqueue', 'Starting']:
                if n_retries > self.fetch_logs_max_retries:
                    logging.error("Max retries reached, can't fetch logs for job %s", job_hash)
                    break
                logging.info("Job status is %s. Retry fetching logs in %d seconds", job_status, self.job_status_interval)
                time.sleep(self.job_status_interval)
                n_retries += 1
            else:
                break

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

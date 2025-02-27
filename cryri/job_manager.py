import io
import logging
from typing import List, Optional
from contextlib import redirect_stdout
from rich.console import Console

try:
    import client_lib
except ImportError:
    logging.warning("client_lib not found. Some functionality may be limited.")

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

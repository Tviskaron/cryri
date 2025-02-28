from typing import Dict, List
from pydantic import BaseModel


class ContainerConfig(BaseModel):
    image: str = None
    command: str = None
    environment: Dict = None
    work_dir: str = None
    run_from_copy: bool = False
    cry_copy_dir: str = None
    exclude_from_copy: List[str] = []


class CloudConfig(BaseModel):
    region: str = "SR006"
    instance_type: str = None
    n_workers: int = 1
    priority: str = "medium"
    description: str = None
    processes_per_worker: int = 1


class CryConfig(BaseModel):
    container: ContainerConfig = ContainerConfig()
    cloud: CloudConfig = CloudConfig()

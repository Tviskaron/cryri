from typing import List, Dict, Annotated

from pydantic import BaseModel, AfterValidator

from cryri.validators import expand_vars_and_user, sanitize_dir_path


class ContainerConfig(BaseModel):
    image: str = None
    command: str = None
    environment: Annotated[Dict, AfterValidator(expand_vars_and_user)] = None

    work_dir: Annotated[
        str,
        AfterValidator(expand_vars_and_user),
        AfterValidator(sanitize_dir_path),
    ] = None

    run_from_copy: bool = False

    cry_copy_dir: Annotated[
        str,
        AfterValidator(expand_vars_and_user),
        AfterValidator(sanitize_dir_path),
    ] = None

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

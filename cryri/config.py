from typing import Optional, List, Dict, Annotated

from pydantic import BaseModel, AfterValidator, ConfigDict

from cryri.validators import expand_vars_and_user, sanitize_dir_path

DEFAULT_REGION = "SR006"


class UvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    cache_dir: Optional[str] = None
    verbose: bool = False


class ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: Optional[str] = None
    command: Optional[str] = None
    setup_command: Optional[str] = None
    environment: Annotated[Optional[Dict], AfterValidator(expand_vars_and_user)] = None

    work_dir: Annotated[
        Optional[str],
        AfterValidator(expand_vars_and_user),
        AfterValidator(sanitize_dir_path),
    ] = None

    run_from_copy: bool = False

    cry_copy_dir: Annotated[
        Optional[str],
        AfterValidator(expand_vars_and_user),
        AfterValidator(sanitize_dir_path),
    ] = None

    exclude_from_copy: List[str] = []

    uv: UvConfig = UvConfig()


class CloudConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str = DEFAULT_REGION
    instance_type: Optional[str] = None
    n_workers: int = 1
    description: Optional[str] = None
    processes_per_worker: int = 1


class CryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: ContainerConfig = ContainerConfig()
    cloud: CloudConfig = CloudConfig()

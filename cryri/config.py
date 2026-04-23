from typing import Optional, List, Dict, Annotated, Union

from pydantic import BaseModel, AfterValidator, ConfigDict, Field, model_validator

from cryri.validators import expand_vars_and_user, sanitize_dir_path

DEFAULT_REGION = "SR006"


class ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: Optional[str] = None
    command: Optional[Union[str, List[str]]] = None
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

    exclude_from_copy: List[str] = Field(default_factory=list)
    parallel: int = 1

    @model_validator(mode="after")
    def validate_command_and_parallel(self):
        if self.parallel < 1:
            raise ValueError("container.parallel must be >= 1")
        if isinstance(self.command, list):
            if not self.command:
                raise ValueError("container.command list must not be empty")
            for idx, cmd in enumerate(self.command, start=1):
                if not isinstance(cmd, str) or not cmd.strip():
                    raise ValueError(f"container.command[{idx}] must be a non-empty string")
        elif isinstance(self.command, str):
            if not self.command.strip():
                raise ValueError("container.command must not be empty")
            if self.parallel != 1:
                raise ValueError("container.parallel is only supported when command is a list")
        return self


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

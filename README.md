# cryri: Job Management and Execution Script

CryRI is a Python-based command-line utility designed for managing containerized jobs in a cloud environment. It provides functionalities to submit, monitor,
and terminate jobs, as well as view logs for individual runs.

## Installation

Install cryri directly from the repository:
```bash
pip install git+https://github.com/Tviskaron/cryri.git
   ```

## Features

### Batching Terminology

To make batching abstractions explicit, cryri uses these names:

- **Submission Unit**: one cloud job submitted by cryri (one `/run_job` API call).
- **Command Batch**: multiple shell commands inside a single Submission Unit.
  - `container.command` is a **string** -> single command (no batching).
  - `container.command` is a **list** -> batched command execution in one job.
  - `container.execution.parallel` controls how many commands run in parallel per batch.

### List Running Jobs


View all currently running jobs in the configured cloud environment:

```bash
cryri --jobs --region SR006
```

### List Available Instance Types For Cloud region


View all currently running jobs in the configured cloud environment:

```bash
cryri --instance_types --region SR006
```


### Submit Jobs

Submit a containerized job using a YAML configuration file:

The configuration file must follow the structure defined below:

```bash
cryri run.yaml
```

```yaml
container:
  image: "cr.ai.cloud.ru/751eb210-f812-4ecf-a18b-6646f0914218/job-custom-image-follower" # Docker image for the job
  command: python3 example.py --map_name test-mazes-s40_wc4_od30 --num_agents 128        # Command to execute in the container
#  environment:                                                                          # Environment variables
#    "WANDB_API_KEY": "<YOUR KEY>"
#    "TEAM_NAME": "<NAME OF YOUR TEAM>"                                                  # Added to job description
  work_dir: '.'                                                                          # Local working directory, recommend leaving as default
  run_from_copy: False                                                                   # Whether to run from a copy of the working directory
  cry_copy_dir: "/home/jovyan/<LOCAL FOLDER>/.cryri"                                     # Local path for creating working directory copies

cloud:
  region: "SR006"                                                                        # Cloud region to deploy the job
  instance_type: "a100plus.1gpu.80vG.12C.96G"                                            # Type of cloud instance
  n_workers: 1                                                                           # Number of worker instances, 1 is only option
  description: "test job"                                                                # Job description
```

Command batching example (single submission, 3 commands in parallel at a time):

```yaml
container:
  image: "cr.ai.cloud.ru/your/image"
  command:
    - "echo start && sleep 1"
    - "echo step-1 && sleep 2"
    - "echo step-2 && sleep 2"
    - "echo step-3 && sleep 2"
    - "echo done"
  execution:
    parallel: 3
  work_dir: "."
cloud:
  region: "SR006"
  instance_type: "cpu.2C.8G"
  n_workers: 1
```

### Typical Use Case

1. **Prepare Docker Image** (see recommendations below)
   - Build your Docker image, tag it with the `job-custom-image-` prefix, and push it to the cloud registry.
   - Example: Use the image `job-custom-image-follower` or create your own with the required prefix.

2. **Set Up Local Directory**
   - Create a local directory containing the code for your job. The simplest way is to clone a repository from GitHub.
     Example for the "Follower" project:
     ```bash
     git clone https://github.com/CognitiveAISystems/learn-to-follow
     ```

3. **Create and Run Job**
   - Inside the folder, create a `run.yaml` file with the necessary configuration. You can use the example provided earlier as a template.
   - Start the job using:
     ```bash
     cryri run.yaml
     ```

4. **Concurrent Experimentation**
   - For multiple concurrent experiments from the same directory, set the `run_from_copy` flag to `True` in `run.yaml`. This ensures each run uses a unique directory, stored at the `cry_copy_dir` path.
     ```yaml
     run_from_copy: True
     ```

5. **Environment variables expansion**
   - Several fields — `environment`, `work_dir`, `cry_copy_dir` — support environment variables `$XXX` and user home directory `~` expansion.
   - With this feature, you can
     - pass environment variables directly to the docker container via `environment` field, e.g. `"WANDB_API_KEY": "$WANDB_API_KEY"`;
     - or use an env var to specify `cry_copy_dir` location `cry_copy_dir: $PERSONAL_HOME/.cryri`.
   - Expansion uses your (=caller) current context.
   - You can also use paths relative to your HOME directory, e.g. `~/path/to/somewhere`. It will be expanded using your `$HOME` variable.

### View Logs

Fetch and display logs for a specific job by providing its hash (or part of it):
```bash
cryri --logs b593837e6a55 --region SR006
```

### Kill Jobs

Terminate a specific running job using its hash:
```bash
cryri --kill b593837e6a55 --region SR006
```

### Recommended workflow with uv + docker
Instead of rebuilding/pushing a new Docker image for every dependency change, keep one shared CUDA base image with uv, and let uv create/update the project environment at job start.
​
To avoid a shared cache on the Jupyter server, always set UV_CACHE_DIR to a directory inside the project (e.g. ${PWD}/.cache/uv), which tells uv where to store its cache.

#### Quickstart (from requirements.txt)
From your project directory on the Jupyter server:

```bash
uv init --bare
UV_CACHE_DIR="${PWD}/.cache/uv" uv add -r requirements.txt
```

From now on, dependency changes are done by editing pyproject.toml or running commands like:

```bash
UV_CACHE_DIR="${PWD}/.cache/uv" uv add <some-package>
```

Create `run_utils/run_init.sh`:

```bash
export UV_CACHE_DIR="${PWD}/.cache/uv" # set up uv cache dir
uv cache dir # log cache dir (debug)
uv sync # sync pyproject.toml <-> uv.lock <-> .venv
uv pip freeze # log all dependencies (debug)
```

Add the script to your command to cryri configuration file:

```
command: bash run_utils/run_init.sh && uv run example.py --map_name test-mazes-s40_wc4_od30 --num_agents 128
```

if you want to use torchrun or just keep using python CLI:
```
command: bash run_utils/run_init.sh && source .venv/bin/activate && python3 example.py --map_name test-mazes-s40_wc4_od30 --num_agents 128

# or

command: bash run_utils/run_init.sh && source .venv/bin/activate && torchrun --nproc_per_node=8 train.py
```

#### Dockerfile example:

```
FROM cr.ai.cloud.ru/aicloud-base-images/cuda12.2-torch2-py310
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
```

Now you can manage your python dependencies right from the Jupyter server. You don't have to create/push new docker images after each dependency change. Just use `UV_CACHE_DIR="${PWD}/.cache/uv" uv add <some-package>` and that's all.

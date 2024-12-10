# cryri: Job Management and Execution Script

CryRI is a Python-based command-line utility designed for managing containerized jobs in a cloud environment. It provides functionalities to submit, monitor,
and terminate jobs, as well as view logs for individual runs.

## Installation

Install cryri directly from the repository:
```bash
pip install git+https://github.com/Tviskaron/cryri.git
   ```

## Features

### List Running Jobs


View all currently running jobs in the configured cloud environment:

```bash
cryri --jobs
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
  work_dir: '.'                                                                          # Local working directory, recommend leaving as default
  run_from_copy: False                                                                   # Whether to run from a copy of the working directory
  cry_copy_dir: "/home/jovyan/<LOCAL FOLDER>/.cryri"                                     # Local path for creating working directory copies

cloud:
  region: "SR006"                                                                        # Cloud region to deploy the job
  instance_type: "a100plus.1gpu.80vG.12C.96G"                                            # Type of cloud instance
  n_workers: 1                                                                           # Number of worker instances, 1 is only option 
```

### Typical Use Case

1. **Prepare Docker Image**  
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


4.	Concurrent Experimentation
For multiple concurrent experiments from the same directory, set the run_from_copy flag to True in run.yaml. This ensures each run uses a unique directory, stored at the `cry_copy_dir` path.



### View Logs

Fetch and display logs for a specific job by providing its hash (or part of it):
```bash
cryri --logs b593837e6a55
```

### Kill Jobs

Terminate a specific running job using its hash:
```bash
cryri --kill b593837e6a55
```
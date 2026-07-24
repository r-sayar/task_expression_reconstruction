#!/bin/bash
#SBATCH --job-name=reconeval_benchmark
#SBATCH --output=reconeval_benchmark_%j.log
#SBATCH --error=reconeval_benchmark_%j.log
#SBATCH --partition=cpu_p         # >>> CLUSTER-SPECIFIC: login/controller partition
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=48:00:00           # the Nextflow controller only orchestrates; the
                                  # heavy per-model jobs are submitted to SLURM by it

# ---------------------------------------------------------------------------
# SLURM wrapper that runs the Nextflow *controller* as a batch job, which then
# submits per-process (per model x latent x seed) jobs via the slurm executor.
#
# Submit with:   sbatch scripts/run_benchmark/submit_hpc.sbatch
#
# This is an alternative to running run_full_hpc.sh directly on a login node;
# use it if your cluster forbids long-running processes on login nodes.
# ---------------------------------------------------------------------------

set -euo pipefail

# >>> CLUSTER-SPECIFIC: load the modules your cluster provides <<<
# module load nextflow java apptainer
# (or activate a conda env that provides them)

export SLURM_CPU_PARTITION="${SLURM_CPU_PARTITION:-cpu_p}"
export SLURM_GPU_PARTITION="${SLURM_GPU_PARTITION:-gpu_p}"
export SLURM_GPU_GRES="${SLURM_GPU_GRES:-gpu:1}"
export SLURM_ACCOUNT="${SLURM_ACCOUNT:-}"

cd "${SLURM_SUBMIT_DIR:-$(pwd)}"

bash scripts/run_benchmark/run_full_hpc.sh

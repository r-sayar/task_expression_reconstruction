#!/bin/bash

# ---------------------------------------------------------------------------
# Launch the full gene-expression-reconstruction benchmark on a SLURM HPC
# cluster with Apptainer/Singularity containers.
#
# Run this from a *cluster login node* (NOT a laptop): it submits per-process
# jobs to SLURM via the Nextflow 'slurm' executor.
#
# Prerequisites on the cluster:
#   * nextflow (>=23.10) and java (>=17) on PATH
#   * apptainer/singularity on PATH
#   * viash on PATH (only if you still need to build; prefer building once)
#   * the real LuCA splits on /lustre (NOT the truncated laptop copy)
#   * containers pulled/converted to .sif (see scripts below)
#
# Set the CLUSTER-SPECIFIC values marked >>> below.
# ---------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# >>> CLUSTER-SPECIFIC: SLURM partitions/account and data root on /lustre <<<
export SLURM_CPU_PARTITION="${SLURM_CPU_PARTITION:-cpu_p}"
export SLURM_GPU_PARTITION="${SLURM_GPU_PARTITION:-gpu_p}"
export SLURM_GPU_GRES="${SLURM_GPU_GRES:-gpu:1}"
export SLURM_ACCOUNT="${SLURM_ACCOUNT:-}"          # optional
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-/lustre/groups/ml01/workspace/reconeval/apptainer_cache}"
export NXF_APPTAINER_CACHEDIR="$APPTAINER_CACHEDIR"

# >>> CLUSTER-SPECIFIC: where the real processed LuCA dataset states live <<<
DATA_ROOT="${DATA_ROOT:-/lustre/groups/ml01/workspace/reconeval/datasets}"
# Each dataset state points at split-specific train/test/solution h5ads and
# carries uns.split = split01|split02|split03 (or a dataset_id containing the
# split token) so run_benchmark can pick the tuned architecture.

RUN_ID="run_$(date +%Y-%m-%d_%H-%M-%S)"
PUBLISH_DIR="${PUBLISH_DIR:-/lustre/groups/ml01/workspace/reconeval/results/${RUN_ID}}"
mkdir -p "$PUBLISH_DIR"

PARAMS_FILE="$(mktemp --tmpdir="${TMPDIR:-/tmp}" params_XXXX.yaml)"
cat > "$PARAMS_FILE" << HERE
input_states: ${DATA_ROOT}/**/state.yaml
rename_keys: 'input_train:output_train;input_test:output_test;input_solution:output_solution'
output_state: "state.yaml"
publish_dir: "${PUBLISH_DIR}"
HERE

echo "Launching benchmark"
echo "  data root:   ${DATA_ROOT}"
echo "  publish dir: ${PUBLISH_DIR}"
echo "  cpu part:    ${SLURM_CPU_PARTITION}   gpu part: ${SLURM_GPU_PARTITION}"

nextflow run . \
  -main-script target/nextflow/workflows/run_benchmark/main.nf \
  -profile hpc \
  -c scripts/nextflow_helpers/labels_hpc.config \
  -entry auto \
  -resume \
  -params-file "$PARAMS_FILE"

echo "Done. Results in ${PUBLISH_DIR}"

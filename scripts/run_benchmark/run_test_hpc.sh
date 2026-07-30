#!/bin/bash

# Submit a small CPU-only smoke test of the benchmark on a Slurm + Apptainer
# HPC cluster, using the -profile hpc / labels_hpc.config path (see
# RUNBOOK.md section 5 for the full explanation).
#
# Prereqs (cluster-specific -- edit the env vars below, or export them
# before calling this script):
#   * viash + nextflow available (module load, or on PATH)
#   * a prebuilt "fat" Apptainer image with this task's Python deps
#     (scikit-learn, scanpy, POT, scikit-misc, ... -- NOT torch/scvi-tools,
#     those are only needed for the gpu-labelled autoencoder/scvi methods)
#   * an input dataset already split into train/test/solution.h5ad, with a
#     *.state.yaml pointing at them (see scripts/create_resources/test_resources.sh)
#
# IMPORTANT: RECON_PYTHONPATH_OPTS must be scoped to this task's own
# component runs only (labels_hpc.config does this via a withName selector)
# -- do NOT put PYTHONPATH in APPTAINER_BIND_OPTS / apptainer.runOptions.
# Fetched openproblems-bio dependency components (e.g. utils/extract_uns_metadata)
# run in their own image with their own Python version; forcing this task's
# PYTHONPATH onto them loads an ABI-incompatible compiled h5py and fails with
# a misleading "partially initialized module" ImportError.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

: "${RECON_ROOT:?set RECON_ROOT to a scratch dir with images/, apptainer_cache/, etc.}"
: "${RECON_SIF:?set RECON_SIF to the prebuilt Apptainer image path}"
: "${RECON_INPUT_STATES:?set RECON_INPUT_STATES to a glob of *.state.yaml input datasets}"

export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$RECON_ROOT/apptainer_cache}"
export APPTAINER_BIND_OPTS="${APPTAINER_BIND_OPTS:---bind /scratch}"
export RECON_PYTHONPATH_OPTS="${RECON_PYTHONPATH_OPTS:---env PYTHONPATH=$RECON_ROOT/pylibs:$RECON_ROOT/ReconEval/src --env PYTHONDONTWRITEBYTECODE=1}"
export SLURM_CPU_PARTITION="${SLURM_CPU_PARTITION:-main}"
export SLURM_QOS="${SLURM_QOS:-standard}"
export NXF_QUEUE_SIZE="${NXF_QUEUE_SIZE:-6}"

echo "viash ns build (target generation only -- no --setup, no docker)"
viash ns build 2>&1 | tail -5

RUN_ID="hpc_smoke_$(date +%Y-%m-%d_%H-%M-%S)"
publish_dir="$RECON_ROOT/results/${RUN_ID}"
mkdir -p "$publish_dir"

cat > "$RECON_ROOT/params_${RUN_ID}.yaml" <<H
input_states: $RECON_INPUT_STATES
rename_keys: 'input_train:output_train;input_test:output_test;input_solution:output_solution'
output_state: state.yaml
publish_dir: $publish_dir
method_ids: [ground_truth, negative_control, pca_l10]
latents: [10]
seeds: [42]
epoch_cap: 1
H

echo "Launching nextflow (CPU-only method subset: ground_truth, negative_control, pca_l10)"
nextflow run . \
  -main-script target/nextflow/workflows/run_benchmark/main.nf \
  -profile hpc \
  -c scripts/nextflow_helpers/labels_hpc.config \
  -entry auto \
  -resume \
  -params-file "$RECON_ROOT/params_${RUN_ID}.yaml"

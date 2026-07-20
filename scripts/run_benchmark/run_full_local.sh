#!/bin/bash

# Run the full gene-expression-reconstruction benchmark over all processed LuCA
# dataset states in resources/datasets/**/state.yaml.
#
# NOTE: depending on the datasets and components (the large latent/width models
# need GPU + high memory), you may need to launch this on a HPC or cloud platform.
# See scripts/run_benchmark/run_full_hpc.sh for a SLURM/Apptainer HPC launch, and
# https://www.nextflow.io/docs/latest/ for more details.

# get the root of the directory
REPO_ROOT=$(git rev-parse --show-toplevel)

# ensure that the command below is run from the root of the repository
cd "$REPO_ROOT"

set -e

echo "Running full benchmark"
echo "  Make sure to run 'viash ns build --parallel --setup cachedbuild' first."

# generate a unique id
RUN_ID="run_$(date +%Y-%m-%d_%H-%M-%S)"
publish_dir="resources/results/${RUN_ID}"

# write the parameters to file
cat > /tmp/params.yaml << HERE
input_states: resources/datasets/**/state.yaml
rename_keys: 'input_train:output_train;input_test:output_test;input_solution:output_solution'
output_state: "state.yaml"
publish_dir: "$publish_dir"
HERE

# run the benchmark
nextflow run . \
  -main-script target/nextflow/workflows/run_benchmark/main.nf \
  -profile docker \
  -resume \
  -entry auto \
  -c common/nextflow_helpers/labels_ci.config \
  -params-file /tmp/params.yaml

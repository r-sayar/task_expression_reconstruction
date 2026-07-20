#!/bin/bash

# Run the gene-expression-reconstruction benchmark on the small LuCA test resources.
# Prereqs:
#   * viash ns build --parallel --setup cachedbuild   (builds target/nextflow/...)
#   * docker available locally
#   * resources_test/reconeval/luca/{train,test,solution}.h5ad present

# get the root of the directory
REPO_ROOT=$(git rev-parse --show-toplevel)

# ensure that the command below is run from the root of the repository
cd "$REPO_ROOT"

set -e

echo "Running benchmark on LuCA test data"
echo "  Make sure to run 'viash ns build --parallel --setup cachedbuild' first."

# generate a unique id
RUN_ID="testrun_$(date +%Y-%m-%d_%H-%M-%S)"
publish_dir="temp/results/${RUN_ID}"

nextflow run . \
  -main-script target/nextflow/workflows/run_benchmark/main.nf \
  -profile docker \
  -resume \
  -c common/nextflow_helpers/labels_ci.config \
  --id luca \
  --input_train resources_test/reconeval/luca/train.h5ad \
  --input_test resources_test/reconeval/luca/test.h5ad \
  --input_solution resources_test/reconeval/luca/solution.h5ad \
  --output_state state.yaml \
  --publish_dir "$publish_dir"

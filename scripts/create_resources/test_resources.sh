#!/bin/bash

# Generate the small LuCA test resources used by component tests and the
# run_test_local benchmark. Produces train/test/solution/prediction/score
# fixtures under resources_test/reconeval/luca.

# get the root of the directory
REPO_ROOT=$(git rev-parse --show-toplevel)

# ensure that the command below is run from the root of the repository
cd "$REPO_ROOT"

set -e

RAW_DATA=resources_test/common/luca
DATASET_DIR=resources_test/reconeval/luca

mkdir -p "$DATASET_DIR"

# process dataset (common LuCA h5ad -> train/test/solution)
viash run src/data_processors/process_dataset/config.vsh.yaml -- \
  --input "$RAW_DATA/dataset.h5ad" \
  --output_train "$DATASET_DIR/train.h5ad" \
  --output_test "$DATASET_DIR/test.h5ad" \
  --output_solution "$DATASET_DIR/solution.h5ad"

# run one method (PCA baseline) to produce a prediction fixture
viash run src/methods/pca_reconstruction/config.vsh.yaml -- \
    --input_train "$DATASET_DIR/train.h5ad" \
    --input_test "$DATASET_DIR/test.h5ad" \
    --output "$DATASET_DIR/prediction.h5ad"

# run one metric to produce a score fixture
viash run src/metrics/statistical/config.vsh.yaml -- \
    --input_prediction "$DATASET_DIR/prediction.h5ad" \
    --input_solution "$DATASET_DIR/solution.h5ad" \
    --output "$DATASET_DIR/score.h5ad"

# write manual state.yaml (optional, but convenient)
cat > "$DATASET_DIR/state.yaml" << HERE
id: luca
train: !file train.h5ad
test: !file test.h5ad
solution: !file solution.h5ad
prediction: !file prediction.h5ad
score: !file score.h5ad
HERE

# only run this if you have access to the openproblems-data bucket
aws s3 sync --profile op \
  "$DATASET_DIR" s3://openproblems-data/resources_test/reconeval/luca \
  --delete --dryrun

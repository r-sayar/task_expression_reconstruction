# Gene expression reconstruction

Benchmark metrics and methods for reconstructing gene expression from
single-cell latent representations.

> [!WARNING]
> This README will be overwritten when running the `create_task_readme` script
> (`scripts/create_readme.sh`), which regenerates it from `src/api`.

## Overview

This OpenProblems v2 task evaluates how well methods reconstruct gene
expression from single-cell data. It ports the ReconEval benchmark
(`theislab/ReconEval`) into the OpenProblems task template.

Components:

* **Data processor** (`src/data_processors/process_dataset`) — normalize LuCA
  (Human Lung Cancer Cell Atlas) data, select HVGs, split train/test/solution.
* **Methods** (`src/methods`) — `pca_reconstruction`, `autoencoder`
  (AE/olAE/mlAE via `library_size_mode`) and `scvi` (scVI/mlscVI/nlscVI via
  likelihood/library options).
* **Control methods** (`src/control_methods`) — `ground_truth`,
  `negative_control`.
* **Metrics** (`src/metrics`) — `statistical`, `biological`, `knn_purity`.
* **Workflows** (`src/workflows`) — `process_datasets`, `run_benchmark`.

Dataset scope: **LuCA only** for now.

## Clone the repository

```bash
git clone --recursive git@github.com:openproblems-bio/task_expression_reconstruction.git
```

> [!NOTE]
> If no files are visible in the `common/` submodule after cloning, see
> [`common/README.md`](common/README.md).

## Build and run

```bash
# build all components
viash ns build --parallel --setup cachedbuild

# small test run on LuCA test resources
bash scripts/run_benchmark/run_test_local.sh
```

For more information on OpenProblems v2, see the
[documentation](https://openproblems.bio/documentation/).

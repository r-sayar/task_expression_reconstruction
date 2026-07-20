# HPC benchmark run — handoff

The task components are ported, extended (6 model variants), wired to the tuned
optimal architectures, and multi-seed. Everything below was prepared on a laptop
and **validated by build + Python smoke tests**, but the real benchmark **must
be launched from a cluster login node** — the laptop has no scheduler, no
`/lustre`, and only a truncated LuCA subset.

## What is ready

* `viash ns build` builds all 20 configs (11 components) cleanly.
* AE (none/observed/modeled) and scVI (scvi/mlscvi/nlscvi) run end-to-end on the
  test fixtures; same seed reproduces, different seed diverges.
* `src/workflows/run_benchmark/optimal_settings.yaml` holds the 90 tuned
  architectures (6 models x 5 latents x 3 splits) parsed from `Optimal setting.md`.
* HPC configs/scripts:
  * `scripts/nextflow_helpers/labels_hpc.config` — SLURM executor + Apptainer +
    GPU label + memory/time escalation.
  * `scripts/nextflow_helpers/prepare_containers_hpc.sh` — build images and
    convert to `.sif`.
  * `scripts/run_benchmark/run_full_hpc.sh` — launch on a login node.
  * `scripts/run_benchmark/submit_hpc.sbatch` — submit the Nextflow controller
    as a SLURM batch job (if login-node long processes are disallowed).

## Exact commands to run on the cluster

```bash
# 0. from a cluster login node, in the task repo, on the port-reconeval-scaffold branch
cd /path/to/task_expression_reconstruction
module load nextflow java apptainer viash   # or your cluster's equivalents

# 1. build components (once)
viash ns build --parallel --setup cachedbuild

# 2. prepare containers (build docker images -> apptainer .sif, cached on /lustre)
#    (skip --setup in the script if you prefer Nextflow to pull docker:// at runtime)
export APPTAINER_CACHEDIR=/lustre/groups/ml01/workspace/reconeval/apptainer_cache
bash scripts/nextflow_helpers/prepare_containers_hpc.sh

# 3. set cluster-specific values (partitions / account / data + output on /lustre)
export SLURM_CPU_PARTITION=cpu_p          # <-- your CPU partition
export SLURM_GPU_PARTITION=gpu_p          # <-- your GPU partition
export SLURM_GPU_GRES=gpu:1               # <-- or gpu:a100:1 etc.
export SLURM_ACCOUNT=ml01                 # <-- if your cluster requires it
export DATA_ROOT=/lustre/groups/ml01/workspace/reconeval/datasets   # real LuCA splits
export PUBLISH_DIR=/lustre/groups/ml01/workspace/reconeval/results/$(date +%F_%H-%M-%S)

# 4a. launch directly on the login node:
bash scripts/run_benchmark/run_full_hpc.sh

# 4b. OR submit the controller as a batch job:
sbatch scripts/run_benchmark/submit_hpc.sbatch
```

The single Nextflow invocation underneath (from `run_full_hpc.sh`) is:

```bash
nextflow run . \
  -main-script target/nextflow/workflows/run_benchmark/main.nf \
  -profile hpc \
  -c scripts/nextflow_helpers/labels_hpc.config \
  -entry auto \
  -resume \
  -params-file <generated params.yaml>
```

## Small test run first (recommended)

Before the full grid, sanity-check the wiring on the test fixtures:

```bash
bash scripts/run_benchmark/run_test_local.sh   # docker profile, tiny LuCA fixtures
```

## Data layout expected on /lustre

`DATA_ROOT/**/state.yaml` — one processed dataset state per split. Each state
provides `input_train` / `input_test` / `input_solution` h5ads, and the solution
`.uns` must carry either `split: split01|split02|split03` or a `dataset_id`
containing a `split0N` token, so `run_benchmark` picks the tuned architecture for
that split. Generate these with `src/workflows/process_datasets` from the full
LuCA common dataset on `/lustre` (NOT the truncated laptop copy).

## Resource sizing

Per Viash labels (mapped to SLURM in `labels_hpc.config`):

| component               | labels                          | GPU | mem (attempt 1) | walltime |
|-------------------------|---------------------------------|-----|-----------------|----------|
| autoencoder (AE family) | hightime, highmem, midcpu, gpu  | yes | 100 GB          | 12 h     |
| scvi (scVI family)      | hightime, highmem, midcpu, gpu  | yes | 100 GB          | 12 h     |
| pca_reconstruction      | midtime, midmem, midcpu         | no  | 50 GB           | 4 h      |
| process_dataset         | highmem, midcpu, midtime        | no  | 100 GB          | 4 h      |
| statistical / metrics   | mid/high mem, midcpu            | no  | 50–100 GB       | 4 h      |
| controls                | lowtime, lowmem, lowcpu         | no  | 20 GB           | 1 h      |

The largest models (latent 2048 / width 4096) are the AE/scVI GPU jobs; memory
and walltime escalate on retry (x task.attempt, up to `veryhighmem` = 200 GB via
the retry ladder). Adjust partition limits / `--gres` type to your cluster.

## Grid size

Per split: 6 models x 5 latents x 5 seeds = **150 GPU training runs** + 5 PCA
baselines + 2 controls = 157 method runs, each scored by `statistical`. Across
3 splits that is ~471 method runs. Budget GPU-hours accordingly.

## Averaging over seeds

Each score is written with the base `method_id` (e.g. `ae_l32`, shared across
seeds) and the `seed` in its `.uns`. Averaging (mean ± std over the 5 seeds) is
done downstream by the standard OpenProblems results-processing step, which
groups by `method_id`. Per-seed Nextflow run ids include `_s<seed>` so outputs
and checkpoints never collide.

## Still needs cluster-specific values

* SLURM partition names, account, and GPU `--gres` string.
* `/lustre` paths for `APPTAINER_CACHEDIR`, `DATA_ROOT`, `PUBLISH_DIR`.
* Module names in `submit_hpc.sbatch` (nextflow/java/apptainer/viash).
* Confirm the GPU partition's max walltime ≥ the largest model's need
  (bump `veryhightime` = 24 h if a single 400-epoch run needs it).

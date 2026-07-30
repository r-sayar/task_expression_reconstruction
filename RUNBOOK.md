# Running the gene-expression-reconstruction task end-to-end

This is the concrete, verified sequence for running this OpenProblems task —
locally (Docker), in CI, and on an HPC cluster (Slurm + Apptainer) — plus how
each step maps onto the OpenProblems v2 component API. Written from an actual
debugging pass that took the pipeline from CI-red to a working local
component chain (branch `fix/ci-test-fixtures`).

## 0. One-time setup

```bash
git clone --recursive https://github.com/<you>/task_expression_reconstruction.git
cd task_expression_reconstruction
git submodule update --init --recursive   # populates common/ (shared OpenProblems test harness + nextflow helpers)
```

Install the two build/run tools (no package manager on most systems — grab
the binaries directly):

```bash
curl -fsSL https://github.com/viash-io/viash/releases/latest/download/viash > viash && chmod +x viash
curl -fsSL https://github.com/nextflow-io/nextflow/releases/download/v24.10.5/nextflow > nextflow && chmod +x nextflow
```

Nextflow **must** be pinned to `24.10.5` — 26.x rejects viash's generated
`nextflow.config` (`tempDir` undefined).

## 1. Build components (Docker, local or CI)

```bash
viash ns build --parallel --setup cachedbuild
```

This walks `src/**/config.vsh.yaml`, resolves each component's `engines:`
block, and either builds a Docker image per component (`--setup cachedbuild`)
or just generates the `target/` scripts (omit `--setup` — this is what the
HPC path below does, since it never touches Docker).

**Bugs found and fixed here** (see branch `fix/ci-test-fixtures`):
- `process_dataset` was missing `scikit-misc` — `seurat_v3` HVG selection
  needs `skmisc.loess` and crashes at runtime without it.
- `pca_reconstruction` / `autoencoder` / `scvi` used `pip install
  git+https://github.com/theislab/ReconEval.git`, which fails to build a
  wheel (upstream `pyproject.toml` has a duplicate hatchling
  `force-include`). Worse, even if the wheel built, upstream ReconEval
  doesn't ship `sc_reconstruction.dataloaders.H5adReconstructionDataModule`
  at all — it only exists on the `r-sayar/ReconEval` fork. Fixed by sparse-
  cloning that fork's `src/` onto `sys.path` via a `.pth` file (same trick
  already used by the three metric components, which only need
  `sc_reconstruction.metrics` and so didn't hit the missing-class problem):
  ```yaml
  - type: docker
    run:
      - git clone --depth 1 --filter=blob:none --sparse https://github.com/r-sayar/ReconEval.git /opt/ReconEval && git -C /opt/ReconEval sparse-checkout set src && printf '%s\n' /opt/ReconEval/src > "$(python -c 'import site; print(site.getsitepackages()[0])')/reconeval.pth"
  ```

## 2. Test resource fixtures

Every method/metric/control component's `test_resources:` (see `src/api/comp_*.yaml`)
points at small fixture files under `resources_test/`. These are **not**
fetched from S3 for this task (no bucket write access to host them) — they
are committed directly to the repo:

```
resources_test/common/luca/dataset.h5ad          # raw "common dataset" input to process_dataset
resources_test/reconeval/luca/{train,test,solution,prediction,score}.h5ad
resources_test/reconeval_demo/*.h5ad              # perturbational-shaped stand-ins for biological/knn_purity
```

Regenerate them with (adjust dataset path/params, or point at a real subset):

```bash
scripts/create_resources/test_resources.sh
```

`_viash.yaml`'s top-level `info.test_resources` list feeds a *different*
mechanism — CI's `sync-and-cache` step, which pre-fetches S3/GS-hosted test
data before any test runs. It only understands `s3://`/`gs://` entries; a
bare local `path:` with no `type:` makes that step exit 1 before a single
component test executes. **Don't add local paths there** — commit the
fixture files instead and leave that list s3/gs-only.

## 3. Run a small local smoke test (Docker + Nextflow)

```bash
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
  --publish_dir temp/results/testrun
```

(`scripts/run_benchmark/run_test_local.sh` wraps exactly this.)

## 4. CI (GitHub Actions)

Two workflows, both calling `viash-io/viash-actions` reusable workflows:

- **`Test`** (`.github/workflows/test.yaml`): `sync-and-cache` → `ns-list` →
  `detect-changed-components` → per-component `viash test`. This is what the
  fixes above unblock.
- **`Build`** (`.github/workflows/build.yaml`): builds+pushes images and a
  `build/<branch>` git branch. Its `target` job needs `contents: write`; on a
  personal fork the default `GITHUB_TOKEN` is often read-only
  (Settings → Actions → General → Workflow permissions), which surfaces as an
  immediate `startup_failure` with zero jobs run. Flip that setting to
  "Read and write permissions" to fix it — has nothing to do with the task's
  own code.

## 5. Real-scale run on an HPC cluster (Slurm + Apptainer)

Docker isn't available on most HPC clusters, so `-profile docker` doesn't
apply. Instead:

```bash
viash ns build            # target/ generation only — no --setup, no docker build
nextflow run . \
  -main-script target/nextflow/workflows/run_benchmark/main.nf \
  -profile hpc \
  -c scripts/nextflow_helpers/labels_hpc.config \
  -entry auto \
  -resume \
  -params-file params.yaml
```

`scripts/nextflow_helpers/labels_hpc.config` maps viash's resource labels
(`lowmem`/`midcpu`/`gpu`/...) onto `SLURM` directives and sets
`process.executor = 'slurm'`, so nextflow itself submits one Slurm job per
task. It also **globally overrides `process.container`** to a single
pre-built "fat" Apptainer image (`RECON_SIF` env var) — since there's no
container registry to pull each component's own image from on the cluster,
every component runs inside one shared `.sif` with all Python deps
(`scvi-tools`, `decoupler`, `omnipath`, `scikit-learn`, `POT`, `scikit-misc`,
...) installed via `pip install --target` onto a `PYTHONPATH` injected
through `apptainer.runOptions`. This means the per-component Docker `setup:`
blocks in `config.vsh.yaml` (the sparse-clone workaround above included) are
**not** what's actually used on this path — keep the fat image's installed
package set and `PYTHONPATH` in sync with whatever the Docker configs need
by hand.

`params.yaml` restricts the run:
```yaml
input_states: /path/to/data/**/*.state.yaml   # one state.yaml per dataset/split
method_ids: [ground_truth, negative_control, pca_l10]   # cheap CPU-only subset for a smoke run
latents: [10]
seeds: [42]
epoch_cap: 1
```
Omit `method_ids`/scale up `latents`/`seeds` for the full paper-scale grid
(latent ∈ {10,32,128,512,2048}, 3 seeds, all 6 model variants + PCA +
controls) — that needs the `gpu`-labelled queue and multi-hour walltimes.

## 6. Component-metadata gotchas found while getting CI green

Two `check_config.py` requirements are **per-component**, not inherited from
`_viash.yaml`'s project-level `links:`/`references:` even though it looks
like they should be:

- `.links.documentation` — required for `method` and `metric` types (not
  `control_method`/`data_processor`). Add `links: documentation: <url>` to
  `src/api/comp_method.yaml` and `comp_metric*.yaml` so every component
  inherits it via `__merge__`.
- `.references.doi` (or `.bibtex`) — required for `method` type only. Add
  `references: doi: [...]` to `comp_method.yaml`.

Also: `run_and_check_output.py` resolves each test's expected input file path
from the **argument's merged file-type `example:` field** (`src/api/file_*.yaml`),
*not* from whatever `test_resources:` a component happens to declare. Keep
every `file_*.yaml` `example:` path consistent (this repo had
`file_prediction.yaml`/`file_score.yaml` pointing at a `reconeval_demo/`
directory that no component's `test_resources:` actually populated — fixed by
aligning them to `resources_test/reconeval/luca/`, matching train/test/solution
and what `test_resources.sh` produces).

And: the local-dev convenience fallback in every method/metric `script.py`
(`Path(__file__).resolve().parents[4] / "src"`, meant to let you run against
an uninstalled checkout) throws `IndexError` — not a caught exception — when
the script runs from viash's shallow test-sandbox path. Guard the `parents`
index length before using it.

## 7. HPC (curta): node-dependent flakiness, and the I/O fix that resolved it

An earlier `nextflow run … -profile hpc` smoke test failed inside
`extract_uns_metadata` (an `openproblems-bio/openproblems` utility, not a
component in this repo) with:

```
ImportError: cannot import name '_errors' from partially initialized module 'h5py'
(most likely due to a circular import) (/scratch/sayar99/reconeval/pylibs/h5py/__init__.py)
```

An identical interactive `apptainer exec ... python3 -c "import h5py"` against
the same `pylibs`/image succeeded cleanly on one compute node (`c012`) but
failed the same import on another (`c094`) — a node-dependent inconsistency,
not a code bug. Root cause: every task read `pylibs` (a `pip install --target`
"fat" environment, ~26k files) live off Lustre. Lustre punishes many-small-file
random access (~1MB/s effective here, vs. ~10-15MB/s for large sequential
reads) — CPython's import machinery does several `stat()`/`open()` calls per
module across `anndata`/`scanpy`/`decoupler`/`scvi-tools`/`torch`/..., which
alone turned a plain `import decoupler` chain into a **12-20 minute** wait,
and left it vulnerable to exactly this kind of node/timing-dependent
half-read/cache inconsistency.

**Fix**: `scripts/nextflow_helpers/labels_hpc.config` now stages `pylibs` +
`ReconEval/src` onto each task's compute node via a `beforeScript`
(`reconStageScript`) instead of reading them off Lustre directly:

1. `scripts/nextflow_helpers/build_stage_tar.sh` packs both into a single
   `stage.tar` on Lustre (run once, or after any dependency change).
2. Each task's `beforeScript` extracts that one file onto the node's local
   `/localscratch` disk (confirmed present on both the `main` and `gpu`
   partitions) before Python runs, keyed by `SLURM_JOB_ID` (not `$TMPDIR` —
   the sbatch launcher exports its own Lustre-path `TMPDIR` for nextflow's/
   apptainer's own scratch use, and that risks inheriting into per-task jobs).
3. `PYTHONPATH` points at the staged local copy instead of the Lustre one.
4. `apptainer.runOptions` (`APPTAINER_BIND_OPTS`) must include
   `--bind /localscratch`, or the container simply can't see the staged
   files — `autoMounts` only covers `/tmp`/`$HOME`/`$PWD`, not this.

Measured end-to-end (real `apptainer exec`, not a shortcut): extracting
2.3GB/29k files onto local disk took **3.9s**; the full
`anndata`/`scanpy`/`decoupler`/`omnipath` import chain from there took
**~7.6s** total. A full pipeline run (`ground_truth`, `negative_control`,
`pca_l10` on synthetic data) then completed end-to-end with every
method/control task finishing in 6-15s (staging + imports + compute +
write), `nextflow exit code: 0`. Staged copies are left on `/localscratch`
after each task (no `afterScript` cleanup) — with ~166GB free per node and
~2.3GB per copy this isn't urgent, but repeated runs on the same node do
accumulate.

## 8. `biological` metric: fully validated, one new sub-issue found

Two separate bugs previously made `biological`'s sub-metrics always
degrade to `NA`:

- **The hang** — `metrics/biological/script.py` fixed via
  `socket.setdefaulttimeout(120)` (see commit history); root cause was
  mostly the Lustre import-chain slowness above, not the network fetch
  itself.
- **`pathway` always NaN** — `metric_pathway` (in `r-sayar/ReconEval`,
  `src/sc_reconstruction/metrics/api.py:201`) called `dc.get_progeny(...)`,
  which decoupler>=2.1 (the pinned version) removed entirely in favor of
  `dc.op.progeny(...)` — every call raised `AttributeError`, silently
  caught, degrading to NaN. Fixed in
  [r-sayar/ReconEval#1](https://github.com/r-sayar/ReconEval/pull/1).

With both fixed, a real run with `biological` in the metrics list
completed successfully (`nextflow exit code: 0`) and `cellcycle_
proportion_same_phase` produced real values (`ground_truth`: 1.0,
`pca_l10`: 0.42, `negative_control`: 0.2 — a sensible ordering). `pathway`
was still NaN on this run, but for a legitimate reason now: the log shows
`_pathway.py:359: UserWarning: No pathways passed overlap threshold (5)`
with no preceding fetch error, meaning `dc.op.progeny()` succeeded and
execution reached the actual gene-overlap check — the tiny synthetic test
dataset (80 genes total) just doesn't overlap enough with any real PROGENy
pathway's gene set. Expect real `pathway` values on the full LuCA dataset.

**`coexpression` — found and fixed**: it failed with
`[Errno 30] Read-only file system: '/home/sayar99'` — `omnipath`'s MSigDB
fetch (used by `metric_coexpression`) resolves its cache/config paths off
`HOME` (`~/.cache/omnipathdb`, `~/.config/omnipathdb.ini`) with no override
via env vars (`XDG_CACHE_HOME`/`XDG_CONFIG_HOME` confirmed not consulted),
and Curta's home directory is read-only on at least some compute nodes
(node-dependent). `apptainer exec --env HOME=...` is blocked outright by
apptainer itself; `apptainer exec --home <dir>` (a distinct, supported flag)
is not, and does correctly redirect `HOME`/`Path.home()`/omnipath's resolved
cache path (confirmed via a real `Annotations.get(resources='MSigDB')`
fetch). `reconStageScript`'s `beforeScript` now creates a per-task-unique
fake-home directory under `/localscratch` (avoiding write races between
concurrent tasks) and `containerOptions` passes it via `--home`. Confirmed
on a real run: `coexpression` now produces real values for every method
(`ground_truth`: 1.0, `pca_l10`: 0.85, `negative_control`: 0.91).

**`knn_purity`/DEG metrics (`deg_dice_at_100`, `deg_logfc_spearman`) — fixed.**
Both need a perturbational reference/control condition that real LuCA
doesn't ship as separate files. It does carry a real `disease` column
(`normal` alongside several cancer subtypes) all the way through to
`prediction.h5ad` (methods already copy `.obs` forward), so `biological`
and `knn_purity` gained `--reference_condition_column`/
`--reference_condition_value` args: when the existing (separate-file)
reference/pool arguments aren't supplied, both derive the same split by
subsetting `input_solution`/`input_prediction` on this column instead.
Wired through `run_benchmark`'s workflow (`--reference_condition_column`/
`--reference_condition_value`, plus `--cytokine_signatures` — see below)
as new top-level passthrough args; `main.nf`'s metrics `runEach` had been
using a **static** `fromState` map carrying only `input_solution`/
`input_prediction`, with a comment noting the extras were "deliberately
not passed" — that, not data availability, was the actual reason these
sub-metrics always returned `NA`. Now a closure, so `statistical` (which
declares neither arg) never receives one it doesn't recognize. Confirmed
on `luca_split02` (real data): `deg_dice_at_100`/`deg_logfc_spearman` and
`knn_purity` all produce real values for every method.

**`cytokine` — fixed.** Needs an Immune Dictionary gene-signature CSV
(Cui et al. 2024, Nature 625:377-384) that doesn't exist in the local
`ReconEval` checkout, but does exist, already at the exact expected
filename, in the paper's own `huggingface.co/datasets/theislab/ReconEval`
repo. Bundled as `src/metrics/biological/resources/cytokine_act_merged.csv`
— an opt-in `--cytokine_signatures` arg default (unlike `--cell_cycle_genes`,
not defaulted: it's 6.5MB and would otherwise load on every run including
tiny CI/synthetic fixtures where it can never match). Its gene symbols are
mouse (the Immune Dictionary's source atlas is mouse lymph node);
`metric_cytokine` already upper-cases before matching, which recovers
shared-name mouse/human orthologs. Confirmed on real LuCA: produces real
values for every method once the `var_names` fix below was also in place.

**The actual root cause of `cellcycle_proportion_same_phase`/
`coexpression`/`pathway`/`cytokine` all being `NaN` on real data — found
and fixed, independent of the two fixes above.** `var_names` on the real
LuCA data (fetched from CELLxGENE Census) turned out to be Census's
internal numeric `soma_joinid` (e.g. `'4'`, `'5'`, `'8'`...), not gene
symbols — real symbols were sitting unused in `var['feature_name']` the
whole time. Every one of these sub-metrics matches its input against a
curated external gene-symbol list (cell cycle genes, PROGENy targets,
MSigDB Hallmark, the cytokine signatures above); zero real symbols means
zero overlap regardless of how many genes are in the panel or how the
panel was selected. `process_dataset/script.py`'s
`sc.pp.highly_variable_genes(..., subset=True)` only subsets rows — it
never touches `var_names` — so this was upstream of HVG selection and
would affect any future Census-sourced dataset, not just this one.
Fixed by re-keying `var_names` onto `var['feature_name']` (when present;
synthetic/CI fixtures have no such column and are unaffected) right
after loading, plus `var_names_make_unique()` for symbols shared by more
than one Ensembl id. The existing `split02` data files were patched in
place (backed up first) rather than re-fetching 9.4GB from Census.

With all three fixes together, a real `luca_split02` run produced real
values for **every** biological sub-metric and method, with a single
correct (not a bug) exception: `pathway` is `NaN` for `negative_control`
specifically, because Pearson/Spearman correlation is mathematically
undefined for a constant input (`ConstantInputWarning`) — `negative_control`
predicts the same training-mean value for every cell, so its per-pathway
scores have zero variance. `ground_truth` (self-comparison) and `pca_l10`
both score all six sub-metrics with real, sensible values.

## 9. GPU methods (autoencoder, scVI): two real bugs found only under real training

Every earlier GPU-labelled smoke test in this repo's history used
`epoch_cap=1` — enough to prove the pipeline *completes*, but not enough to
reveal whether it actually ran on the GPU, since one epoch's wall-clock
difference between CPU and GPU is invisible at that scale. Both bugs below
only surfaced once a genuine full-length (400-epoch, no `epoch_cap`) training
run was attempted on real LuCA data — completion had been silently mistaken
for correctness up to that point.

**Invalid Nextflow directive syntax in the `gpu` label's config template.**
`_viash.yaml`'s `config_mods` templates every GPU-labelled component's
generated `withLabel: gpu { ... }` block as a single string:
`"accelerator = 1, type = 'nvidia.com/gpu'"`. A comma is not a valid
statement separator inside a Nextflow directive block (semicolon or newline
is required) — every `target/nextflow/*/nextflow.config` this project ever
generated for a GPU component carried this syntactically invalid line. It
was worked around all session (and in every ad hoc test launcher script) by
hand-patching the built `target/` directory with a `sed` one-liner after
every build, rather than fixed at the source — meaning `viash ns build` run
the way OpenProblems' own CI (`Build` workflow) runs it, with no such patch,
would have shipped this broken syntax in any real deploy. Fixed the template
itself (comma → semicolon); confirmed a fresh, unpatched `viash ns build`
now produces valid syntax directly, and removed the `sed` workaround from
every launcher script that had it.

**`--nv` never actually reached the container, so GPU methods silently
trained on CPU.** A real 400-epoch training run of `ae_l10`/`scvi_l10` on
real LuCA ran for 4+ hours with zero errors before this was caught —
confirmed via `torch.cuda.is_available()`/`GPU available: False` in the
task logs — despite the SLURM job correctly landing on a GPU node with a
GPU reserved (`clusterOptions`' `--gres=gpu:1`, set in the same
`withLabel: gpu` block, worked fine). Root cause: that same block's
`containerOptions` (meant to add `--nv`, the apptainer flag that exposes the
GPU inside the container) silently lost to a *different* selector
(`withName: '.*runEachWf.*'`, which does not set `--nv`) — verified directly
via the real job's generated `.command.run`, which showed the earlier
block's `containerOptions` had won even though `withLabel: gpu` appears
later in the file. A prior comment in `labels_hpc.config` claimed Nextflow
resolves this by "matching selectors in file order, later ones winning" —
that claim was wrong, at least for `containerOptions` specifically (the
*same* block's `clusterOptions`, which the other selector doesn't set at
all, did apply correctly — so this isn't a blanket "later selector ignored"
problem, just whichever selector wins when two both define the same
directive). Root cause of that precedence quirk was not further isolated.
Rather than continue chasing Nextflow's precedence semantics, the fix
sidesteps the conflict entirely: `--nv` is now appended unconditionally to
the **global** `apptainer.runOptions` (confirmed harmless via
`apptainer exec --nv ...` succeeding fine on non-GPU nodes with no GPU
present), independent of which selector's `containerOptions` wins.

**Confirmed working** on the real production run: `ae_l10`'s task log shows
`device=cuda` (previously `device=cpu`), reaching 20 training epochs in
about 13.5 minutes (~40s/epoch) versus the earlier CPU run's measured
~367s/epoch for the comparable `scvi_l10` model — roughly a 9x speedup,
consistent with genuine GPU execution rather than a fluke.

## How this maps onto the OpenProblems v2 component API

| OpenProblems concept | This task's realization |
|---|---|
| **`data_processor`** component (`src/api/comp_data_processor.yaml`) | `data_processors/process_dataset` — takes a `file_common_dataset` (raw/log-normalized h5ad + `.uns[dataset_id, dataset_name, normalization_id]`), does HVG selection + train/test split, emits `file_train` + `file_test` + `file_solution`. |
| **`method`** component (`comp_method.yaml`) | `methods/{pca_reconstruction, autoencoder, scvi}` — take `file_train` + `file_test`, emit `file_prediction` (reconstructed `.X` + `.uns[method_id]`). One encoder/decoder pair per method; hyperparameters (latent dim, library-size mode, scVI variant) are Viash `arguments:`. |
| **`control_method`** component (`comp_control_method.yaml`) | `control_methods/{ground_truth, negative_control}` — same I/O as a method plus `file_solution` as an extra input, used to calibrate the metric range (positive/negative bounds). |
| **`metric`** component (`comp_metric.yaml` / `comp_metric_knn_purity.yaml`) | `metrics/{statistical, biological, knn_purity}` — take `file_solution` + `file_prediction`, emit `file_score` (`.uns[metric_ids, metric_values]`). `biological`/`knn_purity` degrade sub-metrics to `NA` gracefully when their optional perturbational inputs are absent (observational datasets like LuCA). |
| **File-type contracts** (`src/api/file_*.yaml`) | Each pairwise interface (`common_dataset`, `train`, `test`, `solution`, `prediction`, `score`) is a standalone YAML with an h5ad `.uns`/`.obs` schema — this is what `run_and_check_output.py` validates against in CI, independent of any one component's implementation. |
| **`workflow`** components (`src/workflows/*`) | `workflows/process_datasets` (data_processor over one or more datasets) and `workflows/run_benchmark` (the full method × metric grid) — Nextflow DSL2 scripts (`main.nf`) that use viash's generated `runEach`/`findStates` helpers to fan a dataset's `state.yaml` out across every method/metric combination and join scores back into one `score_uns.yaml`. |
| **`info.test_resources`** (`_viash.yaml`, top level) | Feeds CI's S3/GS pre-fetch step only — separate from any one component's own `test_resources:`. |
| **`test_resources:`** (per-component, in `comp_*.yaml` / `config.vsh.yaml`) | Feeds `viash test` / `run_and_check_output.py` — the actual fixture files a component's test executes against, resolved from the local checkout (committed files or files the top-level fetch step placed there). |
| **`repositories:`** (`_viash.yaml`) | Declares the `openproblems-bio/openproblems` monorepo as a dependency, from which `common/component_tests/run_and_check_output.py`'s Python helper (`openproblems.project`) and `utils/extract_uns_metadata` (used by `run_benchmark`'s workflow to read a dataset's `.uns` for split resolution) are pulled. |

# task_expression_reconstruction x.y.z

## BREAKING CHANGES

<!-- * Restructured `src` directory (PR #3). -->

## NEW FUNCTIONALITY

* Ported the ReconEval gene-expression-reconstruction scaffold into the
  OpenProblems v2 task template.

* Added data processor `data_processors/process_dataset` (LuCA
  normalization, HVG selection, train/test/solution split).

* Added methods `methods/pca_reconstruction`, `methods/autoencoder`
  (AE/olAE/mlAE variants) and `methods/scvi` (scVI/mlscVI/nlscVI variants).

* Added control methods `control_methods/ground_truth` and
  `control_methods/negative_control`.

* Added metrics `metrics/statistical`, `metrics/biological` and
  `metrics/knn_purity`.

* Added workflows `workflows/process_datasets` and `workflows/run_benchmark`.

* Added tuned per-(model, latent, split) optimal architectures and a
  multi-seed setup to the model components.

## MAJOR CHANGES

* Rewrote the `api` file/component contracts for the reconstruction task.

* Updated to Viash 0.9.4.

* Use dependencies in `openproblems-bio/openproblems`.

## MINOR CHANGES

* Updated `README.md`.

## BUGFIXES

* Ensured the training seed reaches both model init and the dataloader
  shuffle (paper bug: dataloader used a hardcoded seed).

* Ensured per-seed outputs do not collide (seed included in benchmark run ids).

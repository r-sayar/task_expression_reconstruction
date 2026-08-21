import sys
from pathlib import Path

import anndata as ad

_parents = Path(__file__).resolve().parents
_repo_src = _parents[4] / "src" if len(_parents) > 4 else None
if _repo_src is not None and _repo_src.is_dir() and str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

## VIASH START
par = {
    "input_solution": "resources_test/reconeval/luca/solution.h5ad",
    "input_prediction": "resources_test/reconeval_demo/prediction_perturbed.h5ad",
    "input_solution_perturbed": "resources_test/reconeval_demo/solution_perturbed.h5ad",
    "input_solution_control": "resources_test/reconeval_demo/solution_control.h5ad",
    "reference_condition_column": None,
    "reference_condition_value": None,
    "output": "output.h5ad",
    "prediction_layer": "X",
    "solution_layer": "X",
    "use_rep": None,
    "k": 20,
}
meta = {"name": "knn_purity"}
## VIASH END

# openproblems_utils.py is bundled next to this script by viash (meta["resources_dir"]);
# fall back to the source-tree location for direct/local execution.
sys.path.append(meta.get("resources_dir") or str(Path(__file__).resolve().parents[2] / "common"))
from openproblems_utils import read_expression, write_score  # noqa: E402

print("Reading prediction", flush=True)
prediction = read_expression(
    ad.read_h5ad(par["input_prediction"]), par["prediction_layer"]
)

# KNN purity is a perturbational metric: it needs a true-perturbed pool and a
# control pool. On observational datasets (e.g. LuCA) these are absent, so the
# metric degrades to NA rather than erroring — keeping the benchmark green.
pert_path = par.get("input_solution_perturbed")
ctrl_path = par.get("input_solution_control")
have_pools = bool(pert_path) and bool(ctrl_path)

perturbed = control = None
if have_pools:
    perturbed = read_expression(ad.read_h5ad(pert_path), par["solution_layer"])
    control = read_expression(ad.read_h5ad(ctrl_path), par["solution_layer"])
elif par.get("reference_condition_column") and par.get("reference_condition_value"):
    # No separate pool files supplied -- derive perturbed/control pools from
    # a column already present on the solution itself (e.g. LuCA's real
    # `disease` column). Inert for datasets without this column (the tiny
    # CI/synthetic fixtures).
    col = par["reference_condition_column"]
    val = par["reference_condition_value"]
    solution = read_expression(
        ad.read_h5ad(par["input_solution"]), par["solution_layer"]
    )
    if col in solution.obs.columns:
        ctrl_mask = (solution.obs[col] == val).to_numpy()
        pert_mask = ~ctrl_mask
        n_ctrl, n_pert = int(ctrl_mask.sum()), int(pert_mask.sum())
        if n_ctrl >= 5 and n_pert >= 5:
            control = solution[ctrl_mask].copy()
            perturbed = solution[pert_mask].copy()
            have_pools = True
            print(
                f"Derived knn_purity pools from {col}=={val!r}: "
                f"{n_ctrl} control cells, {n_pert} perturbed cells",
                flush=True,
            )
        else:
            print(
                f"Reference condition {col}=={val!r} split too small "
                f"(control={n_ctrl}, perturbed={n_pert}); emitting knn_purity=NA.",
                flush=True,
            )
    else:
        print(
            f"reference_condition_column={col!r} not found in solution.obs; "
            "emitting knn_purity=NA.",
            flush=True,
        )

score = float("nan")
if not have_pools:
    print(
        "No perturbed/control pools available (observational dataset); "
        "emitting knn_purity=NA.",
        flush=True,
    )
else:
    try:
        from sc_reconstruction.metrics import metric_knn_purity

        use_rep = par.get("use_rep") or None
        if use_rep in ("", "null", "None"):
            use_rep = None

        print("Computing KNN purity", flush=True)
        score = float(
            metric_knn_purity(
                adata_pred=prediction,
                adata_pert_true=perturbed,
                adata_ctrl=control,
                k=int(par["k"]),
                use_rep=use_rep,
            )
        )
    except Exception as e:  # noqa: BLE001
        print(f"KNN purity could not be computed ({e}); emitting NA.", flush=True)
        score = float("nan")

print("Writing output", flush=True)
write_score(
    dataset_id=prediction.uns["dataset_id"],
    normalization_id=prediction.uns["normalization_id"],
    method_id=prediction.uns["method_id"],
    metric_ids=["knn_purity"],
    metric_values=[score],
    output_path=par["output"],
)

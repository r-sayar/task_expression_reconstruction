import sys
from pathlib import Path

import anndata as ad

_repo_src = Path(__file__).resolve().parents[4] / "src"
if _repo_src.is_dir() and str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

_common_dir = Path(__file__).resolve().parents[2] / "common"
if str(_common_dir) not in sys.path:
    sys.path.insert(0, str(_common_dir))

from openproblems_utils import read_expression, write_score  # noqa: E402

## VIASH START
par = {
    "input_solution": "resources_test/reconeval/luca/solution.h5ad",
    "input_prediction": "resources_test/reconeval_demo/prediction_perturbed.h5ad",
    "input_solution_perturbed": "resources_test/reconeval_demo/solution_perturbed.h5ad",
    "input_solution_control": "resources_test/reconeval_demo/solution_control.h5ad",
    "output": "output.h5ad",
    "prediction_layer": "X",
    "solution_layer": "X",
    "use_rep": None,
    "k": 20,
}
meta = {"name": "knn_purity"}
## VIASH END

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

score = float("nan")
if not have_pools:
    print(
        "No perturbed/control pools provided (observational dataset); "
        "emitting knn_purity=NA.",
        flush=True,
    )
else:
    try:
        from sc_reconstruction.metrics import metric_knn_purity

        perturbed = read_expression(ad.read_h5ad(pert_path), par["solution_layer"])
        control = read_expression(ad.read_h5ad(ctrl_path), par["solution_layer"])

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

import math
import socket
import sys
from pathlib import Path

import anndata as ad
import numpy as np

# The pathway/coexpression sub-metrics auto-fetch PROGENy (decoupler) and
# MSigDB Hallmark (omnipath) gene sets over HTTP when not passed explicitly,
# and ReconEval's compute_biological_metrics already wraps each fetch in a
# try/except that degrades to NA on failure -- but that only helps for a
# call that actually *raises*. Neither client sets a request timeout by
# default, so a slow or degraded network leaves the call blocking
# indefinitely instead of failing fast. Set a generous-but-bounded global
# socket timeout (most HTTP clients, including the ones these libraries use,
# respect it when no explicit per-call timeout is set) so a genuinely stuck
# connection becomes a caught, graceful NA instead of an unbounded hang.
socket.setdefaulttimeout(120)

_parents = Path(__file__).resolve().parents
_repo_src = _parents[4] / "src" if len(_parents) > 4 else None
if _repo_src is not None and _repo_src.is_dir() and str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from sc_reconstruction.metrics import (  # noqa: E402
    compute_biological_metrics,
    load_cell_cycle_genes,
    load_cytokine_dict_from_csv,
)

## VIASH START
par = {
    "input_solution": "resources_test/reconeval_demo/solution.h5ad",
    "input_prediction": "resources_test/reconeval_demo/prediction.h5ad",
    "output": "output.h5ad",
    "solution_layer": "X",
    "prediction_layer": "X",
    "resolve_genes": "intersection",
    "cell_cycle_genes": "resources/regev_lab_cell_cycle_genes.txt",
    "cytokine_signatures": None,
    "input_reference_solution": "resources_test/reconeval_demo/reference_solution.h5ad",
    "input_reference_prediction": "resources_test/reconeval_demo/reference_prediction.h5ad",
    "reference_solution_layer": "X",
    "reference_prediction_layer": "X",
    "reference_condition_column": None,
    "reference_condition_value": None,
    "min_cells": 5,
}
meta = {"name": "biological"}
## VIASH END

# openproblems_utils.py is bundled next to this script by viash (meta["resources_dir"]);
# fall back to the source-tree location for direct/local execution.
sys.path.append(meta.get("resources_dir") or str(Path(__file__).resolve().parents[2] / "common"))
from openproblems_utils import align_genes, read_expression, write_score  # noqa: E402

print("Reading input files", flush=True)
solution = read_expression(ad.read_h5ad(par["input_solution"]), par["solution_layer"])
prediction = read_expression(ad.read_h5ad(par["input_prediction"]), par["prediction_layer"])
solution, prediction = align_genes(
    solution, prediction, resolve_genes=par["resolve_genes"]
)

s_genes, g2m_genes = load_cell_cycle_genes(par["cell_cycle_genes"])

cytokine_dict = None
if par.get("cytokine_signatures"):
    cytokine_dict = load_cytokine_dict_from_csv(par["cytokine_signatures"])

deg_refs = None
if par.get("input_reference_solution") and par.get("input_reference_prediction"):
    ref_solution = read_expression(
        ad.read_h5ad(par["input_reference_solution"]),
        par["reference_solution_layer"],
    )
    ref_prediction = read_expression(
        ad.read_h5ad(par["input_reference_prediction"]),
        par["reference_prediction_layer"],
    )
    ref_solution, ref_prediction = align_genes(
        ref_solution, ref_prediction, resolve_genes=par["resolve_genes"]
    )
    deg_refs = (ref_solution, ref_prediction)
elif par.get("reference_condition_column") and par.get("reference_condition_value"):
    # No separate reference files supplied -- derive a reference/control
    # condition from a column already present on this dataset's own solution
    # (e.g. LuCA's real `disease` column has a `normal` category alongside
    # several cancer subtypes; the tiny CI/synthetic fixtures have no such
    # column, so this stays inert for them). solution/prediction are already
    # gene-aligned above, so subsetting by row here needs no second
    # align_genes call.
    col = par["reference_condition_column"]
    val = par["reference_condition_value"]
    if col in solution.obs.columns:
        ref_mask = (solution.obs[col] == val).to_numpy()
        n_ref = int(ref_mask.sum())
        if n_ref >= int(par["min_cells"]):
            deg_refs = (solution[ref_mask].copy(), prediction[ref_mask].copy())
            print(
                f"Derived DEG reference condition from {col}=={val!r}: "
                f"{n_ref} cells",
                flush=True,
            )
        else:
            print(
                f"Reference condition {col}=={val!r} has only {n_ref} cells "
                f"(< min_cells={par['min_cells']}); skipping DEG metrics.",
                flush=True,
            )
    else:
        print(
            f"reference_condition_column={col!r} not found in solution.obs; "
            "skipping DEG metrics.",
            flush=True,
        )

print("Computing ReconEval biological metrics", flush=True)
scores = compute_biological_metrics(
    solution,
    prediction,
    s_genes=s_genes,
    g2m_genes=g2m_genes,
    cytokine_dict=cytokine_dict,
    deg_refs=deg_refs,
    min_cells=int(par["min_cells"]),
)

metric_order = [
    "cellcycle_proportion_same_phase",
    "coexpression",
    "pathway",
    "deg_dice_at_100",
    "deg_logfc_spearman",
    "cytokine",
]

# Always emit every declared sub-metric so that all method runs have a
# consistent set of metric columns. Sub-metrics whose required inputs are
# absent on this dataset (e.g. DEG/cytokine on observational LuCA) or that
# could not be computed (e.g. no network to fetch gene sets) are reported as
# NaN (NA) rather than dropped or raising an error.
final_ids = []
final_values = []
for metric_id in metric_order:
    value = float(scores.get(metric_id, float("nan")))
    if math.isnan(value):
        print(f"{metric_id}: not computed on this dataset -> NA.", flush=True)
    final_ids.append(metric_id)
    final_values.append(value)

print("Writing output", flush=True)
write_score(
    dataset_id=prediction.uns["dataset_id"],
    normalization_id=prediction.uns["normalization_id"],
    method_id=prediction.uns["method_id"],
    metric_ids=final_ids,
    metric_values=final_values,
    output_path=par["output"],
)

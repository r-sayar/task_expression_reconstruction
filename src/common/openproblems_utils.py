"""Shared helpers for OpenProblems ReconEval metric components."""

from __future__ import annotations

import numpy as np
import pandas as pd
from anndata import AnnData


def read_expression(adata: AnnData, layer: str) -> AnnData:
    """Return a view/copy of ``adata`` with the chosen matrix in ``.X``."""
    if layer == "X":
        return adata
    if layer in adata.layers:
        out = adata.copy()
        out.X = adata.layers[layer]
        return out
    if layer in adata.obsm:
        # An obsm entry (e.g. a latent representation) has its own number of
        # columns, unrelated to .var (genes) -- reusing adata.var here would
        # be a shape mismatch (AnnData validates X.shape[1] == len(var)) for
        # any dimensionality other than n_vars. Build a var frame matching
        # the actual matrix width, and carry .uns over: write_score reads
        # prediction.uns["dataset_id"] etc., so dropping it breaks every
        # caller of this branch.
        X = np.asarray(adata.obsm[layer])
        out = AnnData(
            X=X,
            obs=adata.obs.copy(),
            var=pd.DataFrame(index=[f"{layer}_{i}" for i in range(X.shape[1])]),
            uns=dict(adata.uns),
        )
        return out
    raise KeyError(
        f"Layer {layer!r} not found in .X, .layers, or .obsm of AnnData."
    )


def align_genes(
    solution: AnnData,
    prediction: AnnData,
    *,
    resolve_genes: str,
) -> tuple[AnnData, AnnData]:
    """Subset solution and prediction to a shared gene axis."""
    if resolve_genes == "solution":
        genes = solution.var_names
    elif resolve_genes == "intersection":
        genes = solution.var_names.intersection(prediction.var_names)
    else:
        raise ValueError(f"Unknown resolve_genes={resolve_genes!r}")

    if len(genes) == 0:
        raise ValueError("No shared genes between solution and prediction.")

    return solution[:, genes].copy(), prediction[:, genes].copy()


def write_score(
    *,
    dataset_id: str,
    normalization_id: str,
    method_id: str,
    metric_ids: list[str],
    metric_values: list[float],
    output_path: str,
) -> None:
    import anndata as ad

    output = ad.AnnData(
        uns={
            "dataset_id": dataset_id,
            "normalization_id": normalization_id,
            "method_id": method_id,
            "metric_ids": metric_ids,
            "metric_values": [float(v) for v in metric_values],
        }
    )
    output.write_h5ad(output_path, compression="gzip")

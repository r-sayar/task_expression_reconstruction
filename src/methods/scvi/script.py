import sys
from pathlib import Path

import anndata as ad
import numpy as np
import scvi
import torch

# Allow local development (paper monorepo) without rebuilding Docker images.
_repo_src = Path(__file__).resolve().parents[4] / "src"
if _repo_src.is_dir() and str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from sc_reconstruction.dataloaders import H5adReconstructionDataModule  # noqa: E402

## VIASH START
par = {
    "input_train": "resources_test/reconeval/luca/train.h5ad",
    "input_test": "resources_test/reconeval/luca/test.h5ad",
    "output": "output.h5ad",
    "variant": "scvi",
    "n_latent": 10,
    "n_hidden": 128,
    "n_layers": 1,
    "gene_likelihood": None,
    "max_kl_weight": 1.0,
    "max_epochs": 100,
    "seed": 0,
}
meta = {"name": "scvi"}
## VIASH END

# Variant -> (gene_likelihood, use_observed_lib_size).
#   scvi   : standard scVI, ZINB likelihood, observed library size
#   mlscVI : modeled library size (use_observed_lib_size=False)
#   nlscVI : Normal (Gaussian) likelihood VAE with library
_VARIANTS = {
    "scvi": {"gene_likelihood": "zinb", "use_observed_lib_size": True},
    "mlscvi": {"gene_likelihood": "zinb", "use_observed_lib_size": False},
    "nlscvi": {"gene_likelihood": "normal", "use_observed_lib_size": False},
}


def main() -> None:
    seed = int(par["seed"])
    # scvi.settings.seed seeds torch/numpy/python AND the scvi-tools
    # Lightning dataloader (shuffle), so the seed reaches both model init and
    # data ordering.
    scvi.settings.seed = seed
    torch.manual_seed(seed)
    np.random.seed(seed)

    variant = str(par.get("variant", "scvi")).lower()
    if variant not in _VARIANTS:
        raise ValueError(f"Unknown scVI variant {variant!r}; choose from {list(_VARIANTS)}")
    variant_kwargs = dict(_VARIANTS[variant])
    # Allow an explicit gene_likelihood override (optional).
    if par.get("gene_likelihood"):
        variant_kwargs["gene_likelihood"] = par["gene_likelihood"]

    print(">> Load h5ad splits", flush=True)
    dm = H5adReconstructionDataModule(
        train_path=par["input_train"],
        test_path=par["input_test"],
    )
    dm.prepare_data()
    train_adata = dm.train_adata.copy()
    test_adata = dm.test_adata.copy()

    print(
        f">> Setup SCVI variant={variant} ({variant_kwargs}) on train "
        f"{train_adata.shape}, seed={seed}",
        flush=True,
    )
    # The dataset is log1p_cp10k in .X but retains raw counts in the "counts"
    # layer; scVI must train on counts. Fall back to .X only if no counts layer.
    counts_layer = "counts" if "counts" in train_adata.layers else None
    print(f">> scVI reads counts from layer={counts_layer!r}", flush=True)
    scvi.model.SCVI.setup_anndata(train_adata, layer=counts_layer)
    model = scvi.model.SCVI(
        train_adata,
        n_latent=int(par["n_latent"]),
        n_hidden=int(par["n_hidden"]),
        n_layers=int(par["n_layers"]),
        gene_likelihood=variant_kwargs["gene_likelihood"],
        use_observed_lib_size=variant_kwargs["use_observed_lib_size"],
    )
    print(f">> Train for up to {par['max_epochs']} epochs", flush=True)
    plan_kwargs = {}
    if par.get("max_kl_weight") is not None:
        # KL warmup target weight (paper's max_kl_weight).
        plan_kwargs["max_kl_weight"] = float(par["max_kl_weight"])
    model.train(max_epochs=int(par["max_epochs"]), plan_kwargs=plan_kwargs or None)

    print(">> Predict normalized expression on test", flush=True)
    scvi.model.SCVI.setup_anndata(test_adata, layer=counts_layer)
    X_pred = model.get_normalized_expression(
        adata=test_adata,
        return_numpy=True,
        library_size="latent",
    )

    print(">> Write prediction", flush=True)
    output = ad.AnnData(
        X=np.asarray(X_pred, dtype=np.float32),
        obs=test_adata.obs.copy(),
        var=test_adata.var.copy(),
        uns={
            "dataset_id": train_adata.uns["dataset_id"],
            "normalization_id": train_adata.uns["normalization_id"],
            "method_id": meta["name"],
        },
    )
    output.obs_names = test_adata.obs_names
    output.var_names = test_adata.var_names
    output.write_h5ad(par["output"], compression="gzip")


if __name__ == "__main__":
    main()

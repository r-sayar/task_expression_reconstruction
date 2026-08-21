import sys
from pathlib import Path

import anndata as ad
import numpy as np
import scvi
import torch

# Allow local development (paper monorepo) without rebuilding Docker images.
_parents = Path(__file__).resolve().parents
_repo_src = _parents[4] / "src" if len(_parents) > 4 else None
if _repo_src is not None and _repo_src.is_dir() and str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

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
    "min_kl_weight": 0.0,
    "n_steps_kl_warmup": 40000,
    "learning_rate": 1e-4,
    "train_size": 0.9,
    "early_stopping_patience": 5,
    "max_epochs": 100,
    "seed": 0,
}
meta = {"name": "scvi"}
## VIASH END

# Variant -> (gene_likelihood, use_observed_lib_size), for the vanilla
# scvi.model.SCVI variants only. "nlscvi" is handled separately in
# _run_nlscvi() below -- it is not a vanilla SCVI config, see
# config.vsh.yaml's description for why.
_VARIANTS = {
    "scvi": {"gene_likelihood": "zinb", "use_observed_lib_size": True},
    "mlscvi": {"gene_likelihood": "zinb", "use_observed_lib_size": False},
}


def _run_scvi_variant(par, variant, train_adata, test_adata):
    variant_kwargs = dict(_VARIANTS[variant])
    # Allow an explicit gene_likelihood override (optional).
    if par.get("gene_likelihood"):
        variant_kwargs["gene_likelihood"] = par["gene_likelihood"]

    print(
        f">> Setup SCVI variant={variant} ({variant_kwargs}) on train "
        f"{train_adata.shape}",
        flush=True,
    )
    # The dataset is log_cp10k in .X but retains raw counts in the "counts"
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
    # library_size="latent" returns each cell's expected counts scaled by the
    # model's own inferred per-cell library size -- a linear, counts-like
    # scale. The rest of the pipeline (solution + every other method's
    # prediction) is log1p_cp10k. Comparing scVI's raw linear-scale output
    # directly against a log1p-scale solution in the statistical metric
    # (r2/mse/energy_distance) would unfairly penalize scVI on a scale
    # mismatch rather than actual reconstruction quality. Use a fixed
    # library_size=1e4 (matching cp10k) and log1p the result to genuinely
    # match the normalization_id this output claims to carry. This is
    # correct here because this decoder's px_rate genuinely depends on
    # per-cell library size (observed or modeled) -- contrast with
    # _run_nlscvi, where it deliberately does not.
    X_pred = model.get_normalized_expression(
        adata=test_adata,
        return_numpy=True,
        library_size=1e4,
    )
    return np.log1p(X_pred)


def _run_nlscvi(par, train_adata, test_adata):
    """
    The paper's actual "VAE-None" model: a verbatim port of
    theislab/reconstruction's NormalVAE/NormalDecoderSCVI (see
    reconnlscvi_module.py, bundled as a component resource), not vanilla
    scvi.model.SCVI. That decoder's forward() never reads its library
    argument at all -- library size is architecturally absent from the
    network, not numerically neutralized via a constant -- and it trains
    directly on the same log1p_cp10k .X matrix the autoencoder component
    uses, not raw counts.

    An earlier attempt at "no library size" scVI used vanilla
    scvi.model.SCVI with size_factor_key set to a constant column (a real,
    documented scvi-tools mechanism) trained on the counts layer, and it
    badly underperformed both AE and PCA (r2=-1.20 on LuCA split02/l10).
    Porting the paper's real module and fixing the data representation
    (log1p_cp10k, not counts) fixed this: r2=0.93 (AE=0.94), and the
    paper's AE > VAE > PCA ranking now replicates on 5-6/9 core metrics.
    See RECONEVAL_METRICS_COMPARISON.md in the reconeval repo for the full
    investigation.
    """
    from reconnlscvi_module import NormalVAE
    from scvi.module._constants import MODULE_KEYS

    class ReconNLSCVI(scvi.model.SCVI):
        _module_cls = NormalVAE

    print(
        f">> nlscVI: paper's ported NormalVAE module, trained on .X "
        f"(log1p_cp10k, matching the autoencoder component) -- not the "
        f"counts layer -- on train {train_adata.shape}",
        flush=True,
    )
    ReconNLSCVI.setup_anndata(train_adata)
    model = ReconNLSCVI(
        train_adata,
        n_latent=int(par["n_latent"]),
        n_hidden=int(par["n_hidden"]),
        n_layers=int(par["n_layers"]),
        gene_likelihood="normal",
        use_observed_lib_size=True,
    )

    print(
        f">> Train for up to {par['max_epochs']} epochs, with early "
        f"stopping (patience={par['early_stopping_patience']}) matching "
        f"the paper's own training procedure -- not optional for this "
        f"variant, it was empirically required to reproduce the paper's "
        f"ranking",
        flush=True,
    )
    plan_kwargs = {
        "max_kl_weight": float(par["max_kl_weight"]),
        "min_kl_weight": float(par["min_kl_weight"]),
        "n_steps_kl_warmup": int(par["n_steps_kl_warmup"]),
        "lr": float(par["learning_rate"]),
    }
    model.train(
        max_epochs=int(par["max_epochs"]),
        train_size=float(par["train_size"]),
        plan_kwargs=plan_kwargs,
        early_stopping=True,
        early_stopping_monitor="elbo_validation",
        early_stopping_patience=int(par["early_stopping_patience"]),
        early_stopping_min_delta=0.0,
    )

    print(
        ">> Deterministic predict on test (posterior-mean z -> generative -> px.loc)",
        flush=True,
    )
    test_adata = model._validate_anndata(test_adata)
    model.module.eval()
    loader = model._make_data_loader(adata=test_adata, batch_size=2048)
    preds = []
    with torch.no_grad():
        for tensors in loader:
            inference_outputs, _ = model.module.forward(tensors, compute_loss=False)
            z_mean = inference_outputs[MODULE_KEYS.QZ_KEY].loc
            generative_inputs = model.module._get_generative_input(tensors, inference_outputs)
            generative_inputs[MODULE_KEYS.Z_KEY] = z_mean
            gen_out = model.module.generative(**generative_inputs)
            preds.append(gen_out["px"].loc.cpu().numpy())
    return np.concatenate(preds, axis=0)


def main() -> None:
    seed = int(par["seed"])
    # scvi.settings.seed seeds torch/numpy/python AND the scvi-tools
    # Lightning dataloader (shuffle), so the seed reaches both model init and
    # data ordering.
    scvi.settings.seed = seed
    torch.manual_seed(seed)
    np.random.seed(seed)

    variant = str(par.get("variant", "scvi")).lower()
    if variant not in _VARIANTS and variant != "nlscvi":
        raise ValueError(
            f"Unknown scVI variant {variant!r}; choose from "
            f"{list(_VARIANTS) + ['nlscvi']}"
        )

    print(">> Load h5ad splits", flush=True)
    dm = H5adReconstructionDataModule(
        train_path=par["input_train"],
        test_path=par["input_test"],
    )
    dm.prepare_data()
    train_adata = dm.train_adata.copy()
    test_adata = dm.test_adata.copy()

    if variant == "nlscvi":
        X_pred = _run_nlscvi(par, train_adata, test_adata)
    else:
        X_pred = _run_scvi_variant(par, variant, train_adata, test_adata)

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

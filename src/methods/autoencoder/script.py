import sys
from pathlib import Path

import anndata as ad
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

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
    "n_latent": 32,
    "n_hidden": 256,
    "hidden_widths": None,
    "n_layers": 2,
    "library_size_mode": "none",
    "epochs": 40,
    "batch_size": 256,
    "learning_rate": 0.001,
    "seed": 0,
}
meta = {"name": "autoencoder"}
## VIASH END


def _resolve_widths() -> list[int]:
    """Return the per-layer hidden widths.

    Prefer an explicit ``hidden_widths`` list (e.g. "2048,2048,2048,2048",
    which is how the tuned optimal architectures are expressed). Otherwise
    fall back to ``n_layers`` copies of ``n_hidden``.
    """
    hw = par.get("hidden_widths")
    if hw:
        if isinstance(hw, str):
            widths = [int(x) for x in hw.replace("[", "").replace("]", "").split(",") if x.strip()]
        else:
            widths = [int(x) for x in hw]
        if widths:
            return widths
    return [int(par["n_hidden"])] * int(par["n_layers"])


class _Encoder(nn.Module):
    def __init__(self, in_dim: int, widths: list[int], out_dim: int):
        super().__init__()
        layers: list[nn.Module] = []
        prev = in_dim
        for w in widths:
            layers += [nn.Linear(prev, w), nn.BatchNorm1d(w), nn.ReLU(), nn.Dropout(0.1)]
            prev = w
        layers.append(nn.Linear(prev, out_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)


class _Decoder(nn.Module):
    def __init__(self, n_latent: int, widths: list[int], out_dim: int):
        super().__init__()
        layers: list[nn.Module] = []
        prev = n_latent
        for w in widths:
            layers += [nn.Linear(prev, w), nn.BatchNorm1d(w), nn.ReLU(), nn.Dropout(0.0)]
            prev = w
        layers.append(nn.Linear(prev, out_dim))
        self.net = nn.Sequential(*layers)
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, x, library_size=None):
        recon = self.net(x)
        if library_size is not None:
            # observed / modeled library: distribute over genes and rescale
            recon = self.softmax(recon) * library_size
        return recon


class Autoencoder(nn.Module):
    """Autoencoder with optional library-size handling.

    library_size_mode:
      * "none"     -> plain AE                     (AE)
      * "observed" -> library = row-sum of input   (olAE)
      * "modeled"  -> library = exp(l_encoder(x))   (mlAE)

    Mirrors ``sc_reconstruction.models.reconae.Autoencoder`` forward logic.
    """

    def __init__(self, n_vars: int, widths: list[int], n_latent: int, library_size_mode: str):
        super().__init__()
        if library_size_mode not in ("none", "observed", "modeled"):
            raise ValueError("library_size_mode must be 'none', 'observed', or 'modeled'")
        self.library_size_mode = library_size_mode
        self.encoder = _Encoder(n_vars, widths, n_latent)
        self.decoder = _Decoder(n_latent, widths[::-1], n_vars)
        if library_size_mode == "modeled":
            self.l_encoder = _Encoder(n_vars, [widths[0]], 1)

    def forward(self, x):
        z = self.encoder(x)
        if self.library_size_mode == "none":
            return self.decoder(z)
        if self.library_size_mode == "observed":
            library_size = torch.sum(x, dim=1, keepdim=True)
            return self.decoder(z, library_size)
        # modeled
        library_size = torch.exp(self.l_encoder(x))
        return self.decoder(z, library_size)


def main() -> None:
    seed = int(par["seed"])
    torch.manual_seed(seed)
    np.random.seed(seed)

    print(">> Load h5ad splits", flush=True)
    dm = H5adReconstructionDataModule(
        train_path=par["input_train"],
        test_path=par["input_test"],
    )
    dm.prepare_data()
    train_adata = dm.train_adata
    test_adata = dm.test_adata

    X_train = dm.get_train_matrix().astype(np.float32)
    X_test = dm.get_test_matrix().astype(np.float32)

    n_vars = X_train.shape[1]
    widths = _resolve_widths()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = Autoencoder(
        n_vars=n_vars,
        widths=widths,
        n_latent=int(par["n_latent"]),
        library_size_mode=par["library_size_mode"],
    ).to(device)
    optim = torch.optim.Adam(model.parameters(), lr=float(par["learning_rate"]))

    # Seed the DataLoader shuffle explicitly so the seed reaches BOTH model
    # init (above) and the dataloader ordering. (In the paper repo the loader
    # used a hardcoded seed, so different seeds shared one data ordering.)
    loader_gen = torch.Generator()
    loader_gen.manual_seed(seed)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(X_train)),
        batch_size=int(par["batch_size"]),
        shuffle=True,
        generator=loader_gen,
        drop_last=X_train.shape[0] > int(par["batch_size"]),
    )

    print(
        f">> Train AE (mode={par['library_size_mode']}, widths={widths}, "
        f"latent={par['n_latent']}) on {X_train.shape} for {par['epochs']} "
        f"epochs, seed={seed} (device={device})",
        flush=True,
    )
    model.train()
    for epoch in range(int(par["epochs"])):
        total = 0.0
        for (batch,) in loader:
            batch = batch.to(device)
            optim.zero_grad()
            recon = model(batch)
            loss = nn.functional.mse_loss(recon, batch)
            loss.backward()
            optim.step()
            total += float(loss) * batch.shape[0]
        if epoch == 0 or (epoch + 1) % 10 == 0:
            print(f"   epoch {epoch + 1}: loss={total / X_train.shape[0]:.4f}", flush=True)

    print(">> Reconstruct test", flush=True)
    model.eval()
    with torch.no_grad():
        X_pred = model(torch.from_numpy(X_test).to(device)).cpu().numpy()

    print(">> Write prediction", flush=True)
    output = ad.AnnData(
        X=X_pred.astype(np.float32),
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

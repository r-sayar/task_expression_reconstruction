import subprocess
from pathlib import Path

import anndata as ad
import numpy as np

## VIASH START
meta = {
    "executable": "target/executable/methods/scvi/scvi",
    "resources_dir": "resources_test/reconeval/luca",
}
## VIASH END

resources_dir = Path(meta["resources_dir"])
output_path = "nlscvi_variant_test_output.h5ad"

cmd = [
    meta["executable"],
    "--input_train", str(resources_dir / "train.h5ad"),
    "--input_test", str(resources_dir / "test.h5ad"),
    "--output", output_path,
    "--variant", "nlscvi",
    "--n_latent", "5",
    "--n_hidden", "16",
    "--n_layers", "1",
    "--max_epochs", "2",
    "--early_stopping_patience", "100",
    "--seed", "0",
]
print(">> Running nlscvi variant smoke test:", " ".join(cmd), flush=True)
subprocess.run(cmd, check=True)

out = ad.read_h5ad(output_path)
test_in = ad.read_h5ad(resources_dir / "test.h5ad")

assert out.shape == test_in.shape, f"shape mismatch: {out.shape} != {test_in.shape}"
assert out.uns["method_id"] == "scvi", f"unexpected method_id: {out.uns.get('method_id')}"
assert np.isfinite(np.asarray(out.X)).all(), "non-finite values in nlscvi prediction"

print(">> nlscvi variant test passed:", out.shape, flush=True)

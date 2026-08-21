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


def _find(name):
    # test_resources' `dest: resources_test/reconeval/luca` nests the
    # fixtures under resources_dir rather than flattening them into it --
    # search rather than hardcode the exact nesting.
    matches = list(resources_dir.rglob(name))
    assert matches, f"could not find {name!r} anywhere under {resources_dir}"
    return matches[0]


train_h5ad = _find("train.h5ad")
test_h5ad = _find("test.h5ad")

cmd = [
    meta["executable"],
    "--input_train", str(train_h5ad),
    "--input_test", str(test_h5ad),
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
test_in = ad.read_h5ad(test_h5ad)

assert out.shape == test_in.shape, f"shape mismatch: {out.shape} != {test_in.shape}"
assert out.uns["method_id"] == "scvi", f"unexpected method_id: {out.uns.get('method_id')}"
assert np.isfinite(np.asarray(out.X)).all(), "non-finite values in nlscvi prediction"

print(">> nlscvi variant test passed:", out.shape, flush=True)

#!/bin/bash

# ---------------------------------------------------------------------------
# Build the component Docker images and convert them to Apptainer/Singularity
# .sif files for the HPC benchmark run.
#
# Most HPCs cannot run the Docker daemon; they use Apptainer (formerly
# Singularity). Nextflow's apptainer integration can pull docker:// images at
# runtime, but on air-gapped compute nodes it is safer to pre-build the .sif
# files on a node that has network + (optionally) docker, and cache them on
# shared storage.
#
# Two supported paths:
#   A) You have docker on a build node: `viash ns build --setup build` makes the
#      images locally, then `apptainer build` converts each to .sif.
#   B) No docker: let Nextflow's apptainer.cacheDir pull docker:// images at
#      runtime (autoMounts). Then you only need to `viash ns build` (no --setup)
#      and set APPTAINER_CACHEDIR (done in labels_hpc.config / run_full_hpc.sh).
#
# Run from the repo root on a build node.
# ---------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

SIF_DIR="${APPTAINER_CACHEDIR:-/lustre/groups/ml01/workspace/reconeval/apptainer_cache}"
mkdir -p "$SIF_DIR"

echo ">> 1. Build component executables + docker images"
echo "   (drop --setup if you will pull docker:// at runtime instead)"
viash ns build --parallel --setup cachedbuild

echo ">> 2. Enumerate the images the built components reference"
# viash writes the image tag into each target/*/.config.vsh.yaml
IMAGES=$(grep -rhoE 'ghcr\.io/[^"]+|openproblems/base_python:[0-9]+' target 2>/dev/null | sort -u || true)
echo "$IMAGES"

echo ">> 3. Convert each docker image to .sif under ${SIF_DIR}"
for img in $IMAGES; do
  sif_name=$(echo "$img" | tr '/:' '__').sif
  if [ -f "${SIF_DIR}/${sif_name}" ]; then
    echo "   [skip] ${sif_name} exists"
    continue
  fi
  echo "   building ${sif_name} from ${img}"
  # from a local docker daemon:
  apptainer build "${SIF_DIR}/${sif_name}" "docker-daemon://${img}" \
    || apptainer build "${SIF_DIR}/${sif_name}" "docker://${img}"
done

echo ">> Done. .sif files cached in ${SIF_DIR}"
echo "   Note: torch / scvi-tools pip wheels are CUDA-enabled on linux/x86_64,"
echo "   so GPU works inside the container when Nextflow adds --nv (gpu label)."

#!/usr/bin/env bash
# Rebuild the single-file tarball of pylibs + ReconEval/src that
# labels_hpc.config's beforeScript (reconStageScript) extracts onto each
# task's node-local /localscratch before running Python. Run this once after
# any change to pylibs or ReconEval/src (e.g. a dependency upgrade) -- stale
# contents are not detected automatically.
set -euo pipefail
R="${1:-/scratch/sayar99/reconeval}"
cd "$R"
tar -cf stage.tar.new pylibs ReconEval/src
mv stage.tar.new stage.tar
echo "Wrote $R/stage.tar ($(du -h stage.tar | cut -f1))"

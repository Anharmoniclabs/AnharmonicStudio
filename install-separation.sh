#!/usr/bin/env bash
# Add local CPU stem separation. The DAW itself remains a small install.
set -euo pipefail
cd "$(dirname "$0")"

echo "· installing CPU PyTorch + Demucs (about 1 GB)"
uv sync --inexact --extra separate

echo
echo "· verifying the local stem engine"
uv run --no-sync python -c "import torch, demucs; print(f'  torch {torch.__version__} · demucs {demucs.__version__} · {torch.get_num_threads()} CPU threads')"

echo
echo "Stem separation is ready. Model weights download once, on first use."

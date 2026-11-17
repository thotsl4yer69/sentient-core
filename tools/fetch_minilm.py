#!/usr/bin/env python3
"""Fetch all-MiniLM-L6-v2 ONNX weights and the tokenizer.

Writes ``model.onnx`` and ``tokenizer.json`` into the memory models
directory and checks each file's sha256 before it is kept. The memory
engines load only this directory. They do not download at runtime.

    python tools/fetch_minilm.py
    python tools/fetch_minilm.py --dest /opt/sentient-core/models/all-MiniLM-L6-v2

``SENTIENT_MEMORY_MODELS_DIR`` is the directory that contains
``all-MiniLM-L6-v2`` (or the model directory itself). The default
destination is ``<repo>/models/all-MiniLM-L6-v2``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentient.services.memory.minilm import MODEL_NAME, download_model, repo_models_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help=(
            "Directory for model.onnx and tokenizer.json. "
            f"Default: {repo_models_dir()}"
        ),
    )
    args = parser.parse_args()
    dest = args.dest
    if dest is None:
        env_dir = os.environ.get("SENTIENT_MEMORY_MODELS_DIR")
        if env_dir:
            root = Path(env_dir).expanduser()
            dest = root if (root.name == MODEL_NAME or (root / "model.onnx").exists()) else root / MODEL_NAME
        else:
            dest = repo_models_dir()
    path = download_model(dest)
    print(f"MiniLM ready in {path}")


if __name__ == "__main__":
    main()

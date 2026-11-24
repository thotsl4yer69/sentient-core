"""all-MiniLM-L6-v2 embeddings through ONNX Runtime.

This is the same sentence-transformers pipeline the memory store already
used: the uncased MiniLM transformer, mean pooling over the attention
mask, then L2 normalisation. ``sentence-transformers`` encode() of
``all-MiniLM-L6-v2`` includes that Normalize module, so vectors already
in Redis stay valid. Cosine similarity is unchanged for any older
unnormalised vector as well, because search divides by the vector norms.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
MAX_SEQ_LENGTH = 256
HUGGINGFACE_REPO = "sentence-transformers/all-MiniLM-L6-v2"

# Official ONNX export and tokenizer from sentence-transformers/all-MiniLM-L6-v2.
# The graph emits last_hidden_state. Mean pooling and L2 normalisation match
# modules.json (Transformer, Pooling mean, Normalize).
MODEL_FILES = {
    "model.onnx": {
        "url": (
            "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2"
            "/resolve/main/onnx/model.onnx"
        ),
        "sha256": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    },
    "tokenizer.json": {
        "url": (
            "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2"
            "/resolve/main/tokenizer.json"
        ),
        "sha256": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
    },
}


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_models_dir() -> Path:
    """Default models directory at the repository root."""
    return Path(__file__).resolve().parents[1] / "models" / MODEL_NAME


def download_model(dest: Path | None = None) -> Path:
    """Fetch the ONNX model and tokenizer, replacing a file that fails sha256.

    Returns the directory that holds ``model.onnx`` and ``tokenizer.json``.
    """
    target = Path(dest) if dest is not None else repo_models_dir()
    target.mkdir(parents=True, exist_ok=True)
    for name, spec in MODEL_FILES.items():
        path = target / name
        if path.is_file() and sha256_file(path) == spec["sha256"]:
            continue
        partial = path.with_name(name + ".partial")
        request = urllib.request.Request(
            spec["url"],
            headers={"User-Agent": "sc-core"},
        )
        with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        digest = sha256_file(partial)
        if digest != spec["sha256"]:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"{name} sha256 {digest} does not match {spec['sha256']}"
            )
        partial.replace(path)
    return target


def resolve_model_dir(model_name: str = MODEL_NAME, models_dir: str | None = None) -> Path:
    """Find the directory that contains the fetched MiniLM files."""
    name = model_name or MODEL_NAME
    direct = Path(name).expanduser()
    if (direct / "model.onnx").is_file() and (direct / "tokenizer.json").is_file():
        return direct

    roots = []
    for var in ("SC_MODELS_DIR", "SENTIENT_MEMORY_MODELS_DIR"):
        env_dir = os.environ.get(var)
        if env_dir:
            roots.append(Path(env_dir).expanduser())
    if models_dir:
        roots.append(Path(models_dir).expanduser())
    roots.append(Path("models"))
    roots.append(repo_models_dir().parent)
    roots.append(Path("/opt/sentient-core/models"))
    roots.append(Path("/opt/sc/models"))

    seen = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if (root / "model.onnx").is_file() and (root / "tokenizer.json").is_file():
            return root
        nested = root / name
        if (nested / "model.onnx").is_file() and (nested / "tokenizer.json").is_file():
            return nested
    return repo_models_dir()


def _verify_model_dir(model_dir: Path) -> None:
    missing = [name for name in MODEL_FILES if not (model_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"MiniLM files {missing} are not in {model_dir}. "
            "Fetch them with: python -m sc.embed --fetch"
        )
    for name, spec in MODEL_FILES.items():
        digest = sha256_file(model_dir / name)
        if digest != spec["sha256"]:
            raise RuntimeError(
                f"{model_dir / name} sha256 {digest} does not match {spec['sha256']}. "
                "Fetch a clean copy with: python -m sc.embed --fetch"
            )


@lru_cache(maxsize=2)
def _load_tokenizer(path: str) -> Tokenizer:
    tokenizer = Tokenizer.from_file(path)
    # The published tokenizer.json pads to 128. Mean pooling uses the
    # attention mask, and sentence-transformers truncates at 256.
    tokenizer.no_padding()
    tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
    return tokenizer


@lru_cache(maxsize=2)
def _load_session(path: str) -> ort.InferenceSession:
    return ort.InferenceSession(path, providers=["CPUExecutionProvider"])


class MiniLMEncoder:
    """all-MiniLM-L6-v2 sentence embeddings (384-d, L2-normalised), CPU only.

    CPU on purpose: the GPU is reserved for the LLM, STT and TTS, and one
    MiniLM sentence takes a few milliseconds on the Orin's Cortex-A78AE.
    """

    def __init__(self, model_name: str = MODEL_NAME, models_dir: str | None = None):
        self.model_name = model_name or MODEL_NAME
        self.model_dir = resolve_model_dir(self.model_name, models_dir)
        _verify_model_dir(self.model_dir)
        self._tokenizer = _load_tokenizer(str(self.model_dir / "tokenizer.json"))
        self._session = _load_session(str(self.model_dir / "model.onnx"))
        self.embedding_dim = EMBEDDING_DIM

    def get_sentence_embedding_dimension(self) -> int:
        """Return the sentence embedding width. all-MiniLM-L6-v2 is 384."""
        return self.embedding_dim

    def encode(self, text: str, convert_to_numpy: bool = True) -> np.ndarray:
        """Embed one sentence. The result is float32, shape (384,), L2-normalised."""
        if not isinstance(text, str):
            raise TypeError("MiniLMEncoder.encode expects a single string")
        del convert_to_numpy  # always a numpy vector; kept for the old call sites
        encoded = self._tokenizer.encode(text)
        input_ids = np.array([encoded.ids], dtype=np.int64)
        attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
        token_type_ids = np.array([encoded.type_ids], dtype=np.int64)
        if token_type_ids.shape != input_ids.shape:
            token_type_ids = np.zeros_like(input_ids)
        token_embeddings = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]
        mask = attention_mask.astype(np.float32)[..., None]
        summed = (token_embeddings * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        pooled = summed / counts
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.clip(norm, 1e-12, None)
        return pooled[0].astype(np.float32, copy=False)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MiniLM model files for sc-core")
    parser.add_argument("--fetch", action="store_true", help="download and sha256-check the model files")
    parser.add_argument("--dest", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.fetch:
        where = download_model(args.dest)
        print(f"MiniLM ready in {where}")
        return 0
    where = resolve_model_dir()
    _verify_model_dir(where)
    print(f"MiniLM ok in {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

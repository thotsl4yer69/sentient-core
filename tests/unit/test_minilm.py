"""ONNX MiniLM matches the sentence-transformers vectors already in memory."""

import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "minilm_l6_v2_reference.npz"


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=np.float32)
    right = np.asarray(right, dtype=np.float32)
    return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))


def test_torch_is_not_installed():
    assert importlib.util.find_spec("torch") is None
    assert importlib.util.find_spec("sentence_transformers") is None


def test_engines_import_and_match_stored_minilm_vectors():
    """Embeddings stay compatible with vectors produced by sentence-transformers.

    The fixture vectors are ``SentenceTransformer('all-MiniLM-L6-v2').encode``
    with the published modules (mean pooling and L2 normalisation). A cosine
    of 1 against those vectors means an existing Redis embedding still ranks
    the same way.
    """
    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules

    from sentient.services.memory.engine import MemorySystem
    from sentient.services.memory.minilm import MiniLMEncoder

    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules

    encoder = MiniLMEncoder()
    system = MemorySystem()
    assert system.embedding_dim == 384

    spec = importlib.util.spec_from_file_location(
        "legacy_memory",
        ROOT / "services" / "memory.py",
    )
    legacy = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(legacy)
    legacy_system = legacy.MemorySystem()

    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules

    fixture = np.load(FIXTURE)
    sentences = [str(item) for item in fixture["sentences"]]
    reference = fixture["vectors"]

    for impl in (encoder, system.encoder, legacy_system.encoder):
        for sentence, expected in zip(sentences, reference):
            got = impl.encode(sentence, convert_to_numpy=True)
            assert got.shape == (384,)
            assert got.dtype == np.float32
            assert abs(float(np.linalg.norm(got)) - 1.0) < 1e-5
            similarity = _cosine(got, expected)
            assert similarity >= 0.999, (sentence, similarity)

    # Same comparison the episodic index uses: a stored neural-network
    # vector stays nearer a related query than an unrelated one.
    query = np.asarray(encoder.encode("neural networks and deep learning"), dtype=np.float32)
    neural = reference[sentences.index("I love working with neural networks")]
    weather = reference[sentences.index("The weather is cold and rainy today")]
    assert _cosine(query, neural) > _cosine(query, weather)

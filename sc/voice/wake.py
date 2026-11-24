"""openWakeWord wrapper. Default is the stock ``hey_jarvis`` model (the phrase
that was last known to work on nanob); point ``wake_model`` at a trained
``hey_cortana.onnx`` to change the phrase. Never guesses a phrase it has no
model for.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("sc.voice.wake")

FEATURE_MODELS = ("melspectrogram.onnx", "embedding_model.onnx")


def _model_file(name_or_path: str, models_dir: Path) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".onnx":
        return p
    matches = sorted(models_dir.glob(f"{name_or_path}*.onnx"))
    if not matches:
        raise FileNotFoundError(f"no openWakeWord model '{name_or_path}*.onnx' in {models_dir}")
    return matches[-1]


class WakeWord:
    def __init__(self, model: str, models_dir: Path, threshold: float):
        from openwakeword.model import Model

        models_dir = Path(models_dir)
        missing = [f for f in FEATURE_MODELS if not (models_dir / f).is_file()]
        if missing:
            raise FileNotFoundError(f"openWakeWord feature models {missing} missing in {models_dir}")
        path = _model_file(model, models_dir)
        if not path.is_file():
            raise FileNotFoundError(f"wake model {path} not found")
        self.threshold = threshold
        self.name = path.stem
        self._model = Model(
            wakeword_models=[str(path)],
            inference_framework="onnx",
            melspec_model_path=str(models_dir / FEATURE_MODELS[0]),
            embedding_model_path=str(models_dir / FEATURE_MODELS[1]),
        )
        self._key = next(iter(self._model.models))
        logger.info("wake word model %s (threshold %.2f)", self.name, threshold)

    def score(self, frame: np.ndarray) -> float:
        return float(self._model.predict(frame)[self._key])

    def detected(self, frame: np.ndarray) -> bool:
        return self.score(frame) >= self.threshold

    def reset(self) -> None:
        self._model.reset()

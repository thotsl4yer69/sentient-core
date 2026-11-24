"""Silero VAD (v5 ONNX) on CPU, 16 kHz, 512-sample (32 ms) windows.

Matches the reference wrapper: each window is fed with the previous 64 samples
of context prepended, and the recurrent state is carried between calls.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

RATE = 16000
WINDOW = 512
CONTEXT = 64


class SileroVAD:
    def __init__(self, model_path: Path):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"Silero VAD model missing: {path}")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def _window(self, window: np.ndarray) -> float:
        x = np.concatenate([self._context, window[None, :]], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])

    def process(self, pcm16: np.ndarray) -> list[float]:
        """Feed any number of int16 samples; returns one probability per 32 ms window."""
        samples = pcm16.astype(np.float32) / 32768.0
        buf = np.concatenate([self._pending, samples])
        probs = []
        n = len(buf) // WINDOW
        for i in range(n):
            probs.append(self._window(buf[i * WINDOW:(i + 1) * WINDOW]))
        self._pending = buf[n * WINDOW:]
        return probs


class Endpointer:
    """Turns VAD probabilities into "speech started" / "speech ended".

    Speech starts after ``start_ms`` of probability >= threshold; it ends after
    ``end_silence_ms`` below ``threshold - 0.15`` (hysteresis).
    """

    def __init__(self, threshold: float, end_silence_ms: int, start_ms: int = 96):
        self.threshold = threshold
        self.low = max(0.05, threshold - 0.15)
        self.start_windows = max(1, start_ms // 32)
        self.end_windows = max(1, end_silence_ms // 32)
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.heard_speech = False
        self._above = 0
        self._below = 0

    def update(self, probs: list[float]) -> str | None:
        """Returns "start", "end" or None for this batch of windows."""
        event = None
        for p in probs:
            if not self.in_speech:
                self._above = self._above + 1 if p >= self.threshold else 0
                if self._above >= self.start_windows:
                    self.in_speech = True
                    self.heard_speech = True
                    self._below = 0
                    event = "start"
            else:
                self._below = self._below + 1 if p < self.low else 0
                if self._below >= self.end_windows:
                    self.in_speech = False
                    self._above = 0
                    return "end"
        return event

"""sc-voice: the speakerphone loop.

wake word (or VAD) → record until end of speech → whisper.cpp → sc-core (voice
turn) → Piper, sentence by sentence → speaker. Barge-in with the wake word.
"""

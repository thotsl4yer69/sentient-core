"""PulseAudio selection and PCM signal health; no device IO or audio retention."""


def select_devices(sources, sinks, source, sink, usb_source, usb_sink,
                   source_override=None, sink_override=None,
                   default_source=None, default_sink=None):
    """Re-evaluate USB preference on every inventory, including after fallback.

    Explicit overrides are binding. A missing override is reported unavailable;
    it must not silently become a different microphone or speaker.
    """
    microphones = [name for name in sources if not name.endswith('.monitor')]

    def choose(available, current, preferred, override, default):
        if override:
            return override
        for candidate in (preferred, current, default):
            if candidate in available:
                return candidate
        return next(iter(available), current)

    selected_source = choose(microphones, source, usb_source,
                             source_override, default_source)
    selected_sink = choose(sinks, sink, usb_sink, sink_override, default_sink)
    allowed_sources = sources if source_override else microphones
    return (selected_source, selected_sink,
            selected_source in allowed_sources and selected_sink in sinks)


class InputSignalHealth:
    """Detect sustained exact digital zero without classifying room ambience.

    Counts PCM samples, not wall-clock time, so a blocked pipe cannot be mistaken
    for silence. Only aggregate counters survive a call to observe().
    """
    def __init__(self, rate=16000, silence_seconds=8):
        if rate <= 0 or silence_seconds <= 0:
            raise ValueError('Positive rate and silence bound required')
        self.rate = rate
        self.silence_samples = round(rate * silence_seconds)
        self.reset()

    def reset(self):
        self.samples = 0
        self.zero_samples = 0
        self.has_signal = False
        self.state = 'unmeasured'

    def observe(self, pcm):
        if len(pcm) % 2:
            raise ValueError('PCM16 requires complete samples')
        if not pcm:
            return False
        before = self.state
        count = len(pcm) // 2
        self.samples += count
        if any(pcm):
            self.zero_samples = 0
            self.has_signal = True
            self.state = 'signal_present'
        else:
            self.zero_samples += count
            self.state = ('digital_silence' if self.zero_samples >= self.silence_samples
                          else 'signal_present' if self.has_signal else 'checking')
        return before != self.state

    def status(self):
        return {'state': self.state, 'has_signal': self.has_signal,
                'observed_seconds': round(self.samples / self.rate, 2),
                'digital_zero_seconds': round(self.zero_samples / self.rate, 2)}

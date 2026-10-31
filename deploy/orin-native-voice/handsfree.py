"""Local wake listener with opt-in command-only diagnostics; no ambient transcription or network listener."""
from orin.core.progress_watchdog import watchdog
from orin.voice.capture_diagnostics import record as record_capture, stamp as capture_stamp, health as capture_health
import base64
import collections
import json
import os
from pathlib import Path
import queue
import select
import signal
import subprocess
import sys
import threading
import time
import uuid
from orin.voice.telemetry import Telemetry
from orin.voice.audio_devices import InputSignalHealth, select_devices

ROOT = Path('/mnt/orin-ai/run/handsfree')
USB_SOURCE = 'alsa_input.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.mono-fallback'
USB_SINK = 'alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo'
SOURCE = os.environ.get('ORIN_AUDIO_SOURCE', USB_SOURCE)
SINK = os.environ.get('ORIN_AUDIO_SINK', USB_SINK)
WAKE_MODEL = os.environ.get('ORIN_WAKE_MODEL', '/mnt/orin-ai/models/wake/hey_honey.onnx')
WAKE_PHRASE = os.environ.get('ORIN_WAKE_PHRASE', 'Hey Honey')
WAKE_THRESHOLD = float(os.environ.get('ORIN_WAKE_THRESHOLD', '0.5'))
ACTIVE_WAKE_MODEL = WAKE_MODEL if Path(WAKE_MODEL).is_file() else 'hey_jarvis'
ACTIVE_WAKE_PHRASE = WAKE_PHRASE if ACTIVE_WAKE_MODEL == WAKE_MODEL else 'Hey Jarvis'
FRAME = 1280
STOP = False


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data))
    os.replace(temp, path)


def enabled():
    try:
        return json.loads((ROOT / 'control.json').read_text()).get('enabled') is True
    except (OSError, ValueError):
        return False


def set_enabled(value):
    ROOT.mkdir(parents=True, exist_ok=True)
    atomic_json(ROOT / 'control.json', {'enabled': bool(value)})


class Cancelled(Exception):
    pass


class AudioSourceChanged(OSError):
    """Reset wake/VAD state rather than joining audio from different devices."""


def mouth_envelope(pcm, rate):
    """Non-reconstructable amplitude cues from generated reply audio, never input audio."""
    import numpy as np
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.
    step = max(1, int(rate * .05))
    return [round(min(.85, max(0., (float(np.sqrt(np.mean(samples[i:i+step] ** 2))) - .008) * 6)), 3)
            for i in range(0, len(samples), step)]


class Utterance:
    """Bounded voiced command with 240ms pre-roll and 800ms trailing silence."""
    def __init__(self):
        self.pre = collections.deque(maxlen=3)
        self.frames = []
        self.count = self.speech = self.quiet = 0
        self.last_voiced_at = None
        self.first_voiced_at = None
        self.first_voiced_capture_frame = self.first_voiced_wav_frame = None
        self.first_voiced_utc_ns = self.last_voiced_utc_ns = None

    def add(self, frame, probability):
        self.count += 1
        if probability >= .5:
            self.last_voiced_at = time.monotonic()
            self.last_voiced_utc_ns = time.time_ns()
            if self.first_voiced_at is None:
                self.first_voiced_at = self.last_voiced_at
                self.first_voiced_utc_ns = self.last_voiced_utc_ns
                self.first_voiced_capture_frame = self.count - 1
        if not self.frames:
            self.pre.append(frame)
            if probability >= .5:
                self.first_voiced_wav_frame = len(self.pre) - 1
                self.frames.extend(self.pre)
                self.speech = 1
        else:
            self.frames.append(frame)
            if probability >= .5:
                self.speech += 1
                self.quiet = 0
            else:
                self.quiet += 1
        return (self.count >= 150 or (not self.frames and self.count >= 63)
                or (self.frames and self.quiet >= 10))

    def result(self):
        return b''.join(self.frames) if self.speech >= 3 else b''


def stop_process(proc):
    if proc is None:
        return
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
    for stream in (proc.stdin, proc.stdout):
        if stream:
            stream.close()


def worker():
    from orin.voice.stream_worker import worker as stream_worker
    stream_worker()


class Listener:
    def __init__(self):
        self.capture = self.processor = self.playback = None
        self.events = queue.Queue()
        self.phase = 'starting'
        self.error = None
        self.wakes = self.replies = 0
        self.last_write = 0
        self.last_device_check = 0
        self.last_wake = None
        self.providers = []
        self.speech = None
        self.metrics = {}
        self.turn_id = None
        self.notify_queue = queue.Queue(maxsize=1)
        self.last_notified = None
        self.telemetry = Telemetry()
        self.diagnostic_turn_id = None
        self.last_resource_event = 0
        self.last_reply_end = None
        self.capture_source = None
        self.input_signal = InputSignalHealth()

    def trace(self, event, **fields):
        def active(proc):
            return proc is not None and proc.poll() is None
        state = dict(phase=self.phase, capture_active=active(self.capture),
                     playback_active=active(self.playback), worker_active=active(self.processor),
                     event_queue_depth=self.events.qsize(), notify_queue_depth=self.notify_queue.qsize(),
                     wake_suppressed=self.phase != 'wake_listening' or not active(self.capture), wake_count=self.wakes, reply_count=self.replies)
        state.update(fields)
        self.telemetry.emit(event, self.diagnostic_turn_id, **state)

    def notify_core(self):
        from orin.cli.client import OrinClient
        while True:
            state = self.notify_queue.get()
            try:
                with OrinClient() as client:
                    client.notify_voice_state(state)
            except Exception:
                pass

    def status(self, phase=None, error=None):
        if phase is not None:
            self.phase, self.error = phase, error
            core_state = ('speaking' if phase == 'speaking' else 'listening' if phase == 'listening'
                          else 'thinking' if phase in ('thinking','transcribing','synthesizing') else 'idle')
            if core_state != self.last_notified:
                try:
                    self.notify_queue.get_nowait()
                except queue.Empty:
                    pass
                self.notify_queue.put_nowait(core_state)
                self.last_notified = core_state
        if phase is not None:
            self.trace('phase', error_type=error.split(':', 1)[0] if error else None)
        if time.monotonic() - self.last_resource_event >= 5:
            self.trace('resource_sample')
            self.last_resource_event = time.monotonic()
        now = time.time()
        if phase is not None or now - self.last_write > 1:
            ROOT.mkdir(parents=True, exist_ok=True)
            atomic_json(ROOT / 'status.json', {
                'enabled': enabled(), 'phase': self.phase, 'error': self.error,
                'wake_phrase': ACTIVE_WAKE_PHRASE, 'wake_model': ACTIVE_WAKE_MODEL,
                'wake_threshold': WAKE_THRESHOLD, 'updated_at': now, 'pid': os.getpid(),
                'wake_count': self.wakes, 'reply_count': self.replies, 'last_wake_at': self.last_wake,
                'capture_active': self.capture is not None and self.capture.poll() is None,
                'playback_active': self.playback is not None and self.playback.poll() is None,
                'source': SOURCE, 'sink': SINK, 'providers': self.providers,
                'input_signal': self.input_signal.status(),
                'speech': self.speech, 'metrics': self.metrics, 'turn_id': self.turn_id,
                'capture_diagnostics': capture_health(), 'telemetry': self.telemetry.health(), 'cloud_fallback_configured': True})
            self.last_write = now

    def check(self):
        if STOP or not enabled():
            raise Cancelled()
        self.status()
        watchdog.pulse("voice-control")

    def pause(self, duration):
        until = time.monotonic() + duration
        while time.monotonic() < until:
            self.check()
            time.sleep(.1)

    @staticmethod
    def _pulse_devices(kind):
        result = subprocess.run(['pactl', 'list', 'short', kind], capture_output=True,
                                text=True, timeout=3)
        if result.returncode:
            return []
        return [line.split()[1] for line in result.stdout.splitlines()
                if len(line.split()) > 1]

    @staticmethod
    def _pulse_default(label):
        result = subprocess.run(['pactl', 'info'], capture_output=True, text=True, timeout=3)
        if result.returncode:
            return None
        prefix = label + ':'
        return next((line.split(':', 1)[1].strip() for line in result.stdout.splitlines()
                     if line.startswith(prefix)), None)

    def devices_ready(self):
        """Prefer the known USB headset, but remain usable on PulseAudio defaults."""
        global SOURCE, SINK
        sources, sinks = self._pulse_devices('sources'), self._pulse_devices('sinks')
        defaults = {}
        if SOURCE not in sources:
            defaults['default_source'] = self._pulse_default('Default Source')
        if SINK not in sinks:
            defaults['default_sink'] = self._pulse_default('Default Sink')
        SOURCE, SINK, ready = select_devices(
            sources, sinks, SOURCE, SINK, USB_SOURCE, USB_SINK,
            source_override=os.environ.get('ORIN_AUDIO_SOURCE'),
            sink_override=os.environ.get('ORIN_AUDIO_SINK'), **defaults)
        return ready

    def open_capture(self):
        self.check()
        self.capture = subprocess.Popen(['parec', '--raw', '--format=s16le', '--rate=16000',
                                        '--latency-msec=50',
                                        '--channels=1', '--device=' + SOURCE],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True, bufsize=0)
        self.capture_source = SOURCE
        self.input_signal.reset()
        self.trace('capture_open')

    def close_capture(self):
        was_active = self.capture is not None
        stop_process(self.capture)
        self.capture = None
        self.capture_source = None
        if was_active: self.trace('capture_closed')

    def frame(self):
        now = time.monotonic()
        if now - self.last_device_check > 2:
            self.last_device_check = now
            if not self.devices_ready():
                raise OSError('Audio device disconnected')
            if self.capture_source != SOURCE:
                raise AudioSourceChanged('Microphone changed; resetting capture')
        buf = bytearray()
        deadline = time.monotonic() + 3
        while len(buf) < FRAME * 2:
            self.check()
            if time.monotonic() > deadline:
                raise TimeoutError('Microphone stream timed out')
            if select.select([self.capture.stdout], [], [], .1)[0]:
                data = os.read(self.capture.stdout.fileno(), FRAME * 2 - len(buf))
                if not data:
                    raise OSError('Microphone disconnected')
                buf.extend(data)
        pcm = bytes(buf)
        if self.input_signal.observe(pcm):
            silent = self.input_signal.state == 'digital_silence'
            message = 'Microphone is producing digital silence; check input or reconnect USB audio'
            if silent:
                self.error = message
            elif self.error == message:
                self.error = None
            self.trace('input_signal', error_type='DigitalSilence' if silent else None)
            self.status()
        return pcm

    def play(self, pcm, rate):
        self.close_capture()
        self.check()
        if not self.devices_ready():
            raise OSError('Audio unavailable')
        self.playback = subprocess.Popen(['paplay', '--raw', '--format=s16le',
                                         '--latency-msec=50',
                                         '--rate=' + str(rate), '--channels=1', '--device=' + SINK],
                                        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        start_new_session=True)
        if rate == 22050:
            self.speech = {'started_at': time.time() + .05, 'duration': len(pcm) / (rate * 2),
                           'step': .05, 'envelope': mouth_envelope(pcm, rate)}
        # A writer thread prevents a full audio pipe from delaying the mute switch.
        def write_audio():
            try:
                self.playback.stdin.write(pcm)
                self.playback.stdin.close()
            except (OSError, ValueError, AttributeError):
                pass
        threading.Thread(target=write_audio, daemon=True).start()
        self.status('speaking')
        self.trace('playback_start', audio_kind='reply' if rate == 22050 else 'cue', wake_suppressed=True)
        try:
            deadline = time.monotonic() + 48
            while self.playback.poll() is None:
                self.check()
                if time.monotonic() > deadline:
                    raise TimeoutError('Playback timed out')
                time.sleep(.1)
            if self.playback.returncode:
                raise OSError('Audio playback failed')
        finally:
            stop_process(self.playback)
            self.playback = None
            self.speech = None
            if rate == 22050: self.last_reply_end = (time.monotonic_ns(), time.time_ns())
            self.trace('audio_chunk_end', audio_kind='reply' if rate == 22050 else 'cue', wake_suppressed=True)
            self.status('cooldown')

    def start_worker(self):
        if self.processor and self.processor.poll() is None:
            return
        self.stop_worker()
        self.events = queue.Queue(maxsize=32)
        self.processor = subprocess.Popen([sys.executable, '-u', '-m', 'orin.voice.handsfree', '--worker'],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, start_new_session=True,
                                          text=True, bufsize=1)
        proc = self.processor
        stream, events = proc.stdout, self.events
        def read_events():
            try:
                for line in stream:
                    try:
                        event = json.loads(line)
                        while self.processor is proc and proc.poll() is None:
                            try:
                                events.put(event, timeout=.1)
                                break
                            except queue.Full:
                                continue
                    except ValueError:
                        continue
            finally:
                try:
                    events.put_nowait({'worker_stopped': True})
                except queue.Full:
                    pass
        threading.Thread(target=read_events, daemon=True).start()

    def stop_worker(self):
        stop_process(self.processor)
        self.processor = None

    def process(self, audio, endpoint_metrics=None):
        self.start_worker()
        self.turn_id = self.diagnostic_turn_id or uuid.uuid4().hex
        self.diagnostic_turn_id = self.turn_id
        self.last_reply_end = None
        self.metrics = dict(endpoint_metrics or {})
        started = time.monotonic()
        self.status('transcribing')
        self.processor.stdin.write(json.dumps({'id': self.turn_id, 'voice_run_id': getattr(getattr(self, 'telemetry', None), 'run_id', None), 'audio': base64.b64encode(audio).decode()}) + '\n')
        self.processor.stdin.flush()
        deadline = time.monotonic() + 120
        played = False
        try:
            while True:
                self.check()
                if time.monotonic() > deadline:
                    raise TimeoutError('Assistant response timed out')
                try:
                    event = self.events.get(timeout=.1)
                except queue.Empty:
                    if self.processor.poll() is not None:
                        raise RuntimeError('Speech worker stopped')
                    continue
                if event.get('worker_stopped'):
                    raise RuntimeError('Speech worker stopped')
                if event.get('id') != self.turn_id:
                    if not event.get('ready'): self.trace('stale_worker_event', stale=True)
                    continue
                if event.get('telemetry'):
                    detail = event['telemetry']
                    self.trace(detail['event'], worker_mono_ns=detail['mono_ns'], worker_utc_ns=detail['utc_ns'], phrase_queue_depth=detail.get('phrase_queue_depth'), core_request_id=detail.get('core_request_id'))
                if event.get('phase'):
                    self.status(event['phase'])
                if event.get('pcm'):
                    pcm = base64.b64decode(event['pcm'], validate=True)
                    rate = event.get('rate', 22050)
                    if rate != 22050 or len(pcm) > rate * 2 * 30:
                        raise ValueError('Invalid synthesis audio')
                    self.metrics.setdefault('first_playback_request_ms', round((time.monotonic() - started) * 1000))
                    playback_start = time.monotonic()
                    self.play(pcm, rate)
                    deadline += time.monotonic() - playback_start
                    played = True
                if event.get('done'):
                    self.metrics.update(event.get('metrics') or {})
                    self.metrics['processing_and_playback_ms'] = round((time.monotonic() - started) * 1000)
                    if event.get('error'):
                        raise RuntimeError(event['error'])
                    if event.get('mute'):
                        set_enabled(False)
                    self.providers = event.get('providers', [])
                    if played:
                        self.replies += 1
                        if self.last_reply_end:
                            self.trace('playback_end', event_mono_ns=self.last_reply_end[0], event_utc_ns=self.last_reply_end[1])
                    self.trace('turn_complete', cancelled=bool(event.get('cancelled')), outcome='cancelled' if event.get('cancelled') else 'muted' if event.get('mute') else 'replied' if played else 'no_reply')
                    self.status('cooldown')
                    return
        except BaseException:
            self.stop_worker()
            raise
        finally:
            self.speech = None
            self.turn_id = None

    def run(self):
        watchdog.require("voice-control", 90)
        watchdog.start()
        threading.Thread(target=self.notify_core, daemon=True).start()
        import numpy as np
        from openwakeword.model import Model
        from openwakeword.vad import VAD
        detector = Model(wakeword_models=[ACTIVE_WAKE_MODEL], inference_framework='onnx')
        detector_key = next(iter(detector.models))
        vad = VAD()
        cue = (np.sin(2 * np.pi * 660 * np.arange(1920) / 16000) * 1600).astype(np.int16).tobytes()
        while not STOP:
            watchdog.pulse("voice-control")
            try:
                if not enabled():
                    self.close_capture()
                    self.stop_worker()
                    self.status('muted')
                    time.sleep(.25)
                    continue
                if not self.devices_ready():
                    self.status('waiting_for_audio', 'Microphone or speaker unavailable')
                    self.pause(3)
                    continue
                detector.reset()
                self.start_worker()
                self.open_capture()
                self.metrics['rearmed_at'] = time.time()
                self.status('wake_listening')
                self.trace('wake_rearmed', wake_suppressed=False)
                self.diagnostic_turn_id = None
                while True:
                    detector_frame = self.frame()
                    detector_frame_read = capture_stamp()
                    score = detector.predict(np.frombuffer(detector_frame, dtype=np.int16))[detector_key]
                    if score >= WAKE_THRESHOLD:
                        wake_stamp = (time.monotonic_ns(), time.time_ns())
                        break
                self.close_capture()
                capture_meta={'wake_decision':{'mono_ns':wake_stamp[0],'utc_ns':wake_stamp[1]},
                              'wake_detector_frame_read_complete':detector_frame_read,
                              'wake_capture_closed':capture_stamp(),'detector_input_samples':len(detector_frame)//2,
                              'detector_internal_context_samples':'not_available','wake_score':float(score),
                              'acoustic_wake_end':'not_available','clock_semantics':'software observations, not hardware sample timestamps',
                              'pre_wake_audio_saved':False,'sample_rate':16000,'frame_samples':FRAME}
                self.wakes += 1
                self.last_wake = time.time()
                self.diagnostic_turn_id = uuid.uuid4().hex
                self.trace('wake_detected', event_mono_ns=wake_stamp[0], event_utc_ns=wake_stamp[1], wake_suppressed=True)
                capture_meta['cue_requested']=capture_stamp()
                self.play(cue, 16000)
                capture_meta['cue_returned']=capture_stamp()
                capture_meta['cue_samples']=len(cue)//2
                self.pause(.25)
                # Reset VAD's recurrent state between commands; never inspect ambient speech.
                vad.reset_states()
                utterance = Utterance()
                capture_meta['command_capture_open_requested']=capture_stamp()
                self.open_capture()
                capture_meta['command_capture_open_returned']=capture_stamp()
                self.status('listening')
                while True:
                    frame = self.frame()
                    frame_read_stamp=capture_stamp()
                    if utterance.count==0:capture_meta['command_first_frame_read_complete']=frame_read_stamp
                    probability = vad.predict(np.frombuffer(frame, dtype=np.int16), frame_size=320)
                    if probability >= .5 and utterance.first_voiced_at is None:
                        capture_meta['first_voiced_frame_read_complete']=frame_read_stamp
                        capture_meta['first_voiced_vad_probability']=float(probability)
                    if utterance.add(frame, probability):
                        vad_stamp = (time.monotonic_ns(), time.time_ns())
                        break
                self.close_capture()
                audio = utterance.result()
                capture_meta.update(command_capture_closed=capture_stamp(),capture_frames=utterance.count,
                    voiced_frames=utterance.speech,first_voiced_capture_sample=(utterance.first_voiced_capture_frame*FRAME if utterance.first_voiced_capture_frame is not None else None),
                    first_voiced_wav_sample=(utterance.first_voiced_wav_frame*FRAME if utterance.first_voiced_wav_frame is not None else None),
                    first_vad_speech_decision_mono_ns=(round(utterance.first_voiced_at*1e9) if utterance.first_voiced_at is not None else None),
                    first_vad_speech_decision_utc_ns=utterance.first_voiced_utc_ns,
                    wav_start_capture_sample=((utterance.first_voiced_capture_frame-utterance.first_voiced_wav_frame)*FRAME if utterance.first_voiced_capture_frame is not None else None),
                    wake_to_first_vad_ms=((utterance.first_voiced_at*1e9-wake_stamp[0])/1e6 if utterance.first_voiced_at is not None else None),
                    capture_closed_to_reopen_request_ms=(capture_meta['command_capture_open_requested']['mono_ns']-capture_meta['wake_capture_closed']['mono_ns'])/1e6,
                    audio_status='existing_command_segment' if audio else 'no_accepted_command_no_wav',
                    missing_audio_scope='wake-detector audio and closed-capture interval are absent from command WAV; speech there is not measured')
                record_capture('capture',self.diagnostic_turn_id,self.telemetry.run_id,capture_meta,audio)
                self.trace('vad_end', event_mono_ns=vad_stamp[0], event_utc_ns=vad_stamp[1], voiced=bool(audio), wake_suppressed=True)
                self.trace('speech_boundaries', captured_frames=utterance.count, voiced_frames=utterance.speech,
                           frame_ms=80, first_speech_mono_ns=round(utterance.first_voiced_at*1e9) if utterance.first_voiced_at is not None else None,
                           last_speech_mono_ns=round(utterance.last_voiced_at*1e9) if utterance.last_voiced_at is not None else None,
                           first_speech_utc_ns=utterance.first_voiced_utc_ns, last_speech_utc_ns=utterance.last_voiced_utc_ns,
                           silence_tail_ms=(vad_stamp[0]/1e9-utterance.last_voiced_at)*1000 if utterance.last_voiced_at is not None else None)
                if audio:
                    endpoint_metrics = {'endpoint_at': time.time(),
                                        'vad_endpoint_delay_ms': round((time.monotonic() - utterance.last_voiced_at) * 1000)}
                    self.process(audio, endpoint_metrics=endpoint_metrics)
                else:
                    self.trace('turn_complete', outcome='no_voiced_command')
                self.trace('post_playback_guard', wake_suppressed=True)
                self.pause(.5)
            except AudioSourceChanged:
                self.close_capture()
                self.status('recovering_audio', 'Microphone changed; rearming wake detection')
            except Cancelled:
                self.trace('cancelled', cancelled=True)
                self.close_capture()
                stop_process(self.playback)
                self.playback = None
                self.speech = None
                self.stop_worker()
            except Exception as exc:
                self.trace('error', error_type=type(exc).__name__)
                self.close_capture()
                stop_process(self.playback)
                self.playback = None
                self.speech = None
                self.stop_worker()
                self.status('retrying', type(exc).__name__ + ': voice unavailable; retrying')
                try:
                    self.pause(3)
                except Cancelled:
                    pass
        self.close_capture()
        self.stop_worker()
        self.status('stopped')


def main():
    if '--worker' in sys.argv:
        worker()
        return
    if '--enable' in sys.argv or '--mute' in sys.argv:
        set_enabled('--enable' in sys.argv)
        return
    if '--status' in sys.argv:
        print((ROOT / 'status.json').read_text())
        return
    def stop(signum, frame):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    Listener().run()


if __name__ == '__main__':
    main()

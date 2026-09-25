import collections
import logging
import pathlib
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger("zade")

RATE, FRAME = 16000, 1280
FRAME_S = FRAME / RATE


def rms(frame):
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def decide(levels, thr, silence_s, max_s, start_timeout_s, end_ratio=0.25):
    # ponytail: energy endpointing; swap for Silero VAD if noise keeps recordings open.
    n = len(levels)
    loud = [i for i, level in enumerate(levels) if level > thr]
    if not loud:
        return "abort" if n >= round(start_timeout_s / FRAME_S) else "wait"
    if n >= round(max_s / FRAME_S):
        return "stop"
    # Someone talking in the background stays above the room-noise threshold and kept recordings open.
    # The user, close to the mic, is much louder: they have finished once the sound falls well below
    # their own voice level, whatever the room is doing.
    voice = float(np.median([levels[i] for i in loud]))
    end_thr = max(thr, end_ratio * voice)
    last = max(i for i, level in enumerate(levels) if level > end_thr)
    return "stop" if n - 1 - last >= round(silence_s / FRAME_S) else "wait"


def open_stream(wait_s=120, retry_s=1.0):
    """The microphone stream. Right after login the audio session may not be up yet: wait for it here
    instead of crashing (a restart reloads every model)."""
    waited = 0.0
    while True:
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME)
            stream.start()
            return stream
        except sd.PortAudioError as e:
            if waited >= wait_s:
                raise
            if waited == 0:
                log.warning("microphone not available yet, waiting: %s", e)
            time.sleep(retry_s)
            waited += retry_s
            sd._terminate()  # PortAudio lists devices once: re-initialise to see the new one
            sd._initialize()


def read(stream):
    data, _ = stream.read(FRAME)
    return data[:, 0]


def wake_model(cfg):
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    w = cfg["wake"]
    if w["model"].endswith(".onnx"):
        model = str(pathlib.Path(w["model"]).expanduser())
    else:
        model = w["model"]  # built-in name such as "hey_jarvis"
        download_models([model])
    kw = {}
    if w["verifier"]:
        kw = {"custom_verifier_models": {pathlib.Path(model).stem: str(pathlib.Path(w["verifier"]).expanduser())},
              "custom_verifier_threshold": w["verifier_threshold"]}
    if w.get("vad_threshold"):
        kw["vad_threshold"] = w["vad_threshold"]  # Silero VAD: scores only count during real speech
    return Model(wakeword_models=[model], inference_framework="onnx", **kw)


def speech_threshold(noise_levels, floor, factor=2.5):
    """Loudness that counts as speech: a multiple of the room's noise, never below the configured floor."""
    if not noise_levels:
        return floor
    # 20th percentile: the quiet moments, even if you were talking for most of the window.
    return max(floor, round(float(np.percentile(noise_levels, 20)) * factor))


NOISE_FRAMES = 125  # ~10 s of room sound kept while waiting for the wake word
noise = []


RECENT_FRAMES = 25   # ~2 s of audio kept for the wake word's second opinion
COOLDOWN_FRAMES = 12  # ~1 s ignored after a rejected wake, so the same sound can't re-trigger


def wait_for_wake(stream, model, threshold, poll=None, verify=None):
    """Return "wake" on the wake word, or whatever `poll()` returns when it is truthy (e.g. "hotkey").
    With `verify`, a candidate wake only counts if verify(last ~2 s of audio) agrees."""
    model.reset()
    recent = collections.deque(maxlen=RECENT_FRAMES)
    cooldown = 0
    while True:
        f = read(stream)
        recent.append(f)
        noise.append(rms(f))
        del noise[:-NOISE_FRAMES]
        if poll is not None and (reason := poll()):
            return reason
        score = max(model.predict(f).values())  # always fed, so the model's audio history stays continuous
        if cooldown:
            cooldown -= 1
            continue
        if score >= threshold:
            if verify is None or verify(np.concatenate(recent)):
                return "wake"
            log.info("wake rejected by the second check (score %.2f)", score)
            cooldown = COOLDOWN_FRAMES


def record(stream, cfg, start_timeout_s=None, released=None, cancelled=None, on_level=None):
    """Record one utterance. With `released` (push-to-talk), stop when it returns True, not on silence.
    `cancelled` returning True drops the recording (returns None)."""
    a = cfg["audio"]
    thr = speech_threshold(noise, a["rms_threshold"], a["noise_factor"])
    frames, levels = [], []
    while True:
        f = read(stream)
        frames.append(f)
        levels.append(rms(f))
        if on_level is not None:  # 0 at room noise, ~0.8 for normal speech, capped at 1 (overlay waveform)
            room = thr / a["noise_factor"]
            on_level(max(0.0, min(1.0, (levels[-1] - room) / (room * 5))))
        if released is not None:
            if cancelled is not None and cancelled():
                return None
            if released() or len(frames) >= round(a["max_s"] / FRAME_S):
                return np.concatenate(frames)
            continue
        d = decide(levels, thr, a["silence_s"], a["max_s"], start_timeout_s or a["start_timeout_s"],
                   a.get("end_ratio", 0.25))
        if d == "abort":
            return None
        if d == "stop":
            return np.concatenate(frames)


def drain(stream):
    """Drop audio buffered while Zade was speaking, so it never hears itself (FR-4)."""
    if n := stream.read_available:
        stream.read(n)


def chime_wave(style, volume, followup=False):
    """The listening sound. soft: a gentle rising two-tone; classic: a short beep; none: silence.
    Follow-up listening uses a quieter version."""
    if style == "none":
        return None
    peak = volume * 0.4 * (0.5 if followup else 1.0)
    if style == "classic":
        t = np.linspace(0, 0.12, int(RATE * 0.12), False)
        return (peak * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    parts = []
    for freq in ((587, 880) if not followup else (660, 784)):
        t = np.linspace(0, 0.07, int(RATE * 0.07), False)
        envelope = np.sin(np.pi * t / 0.07)  # fade in and out: no click
        parts.append(peak * envelope * np.sin(2 * np.pi * freq * t))
    return np.concatenate(parts).astype(np.float32)


def cue(stream, soft=False, cfg=None):
    """Play the listening sound, then drop it from the mic buffer so it is not recorded as speech."""
    snd = (cfg or {}).get("sound", {"chime": "soft", "volume": 0.6})
    wave = chime_wave(snd["chime"], snd["volume"], followup=soft)
    if wave is not None:
        sd.play(wave, RATE)
        sd.wait()
    drain(stream)

import pathlib

import numpy as np
import sounddevice as sd

RATE, FRAME = 16000, 1280
FRAME_S = FRAME / RATE


def rms(frame):
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def decide(levels, thr, silence_s, max_s, start_timeout_s):
    # ponytail: energy endpointing; swap for Silero VAD if noise keeps recordings open.
    n = len(levels)
    loud = [i for i, level in enumerate(levels) if level > thr]
    if not loud:
        return "abort" if n >= round(start_timeout_s / FRAME_S) else "wait"
    if n >= round(max_s / FRAME_S):
        return "stop"
    return "stop" if n - 1 - loud[-1] >= round(silence_s / FRAME_S) else "wait"


def open_stream():
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME)
    stream.start()
    return stream


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
    return Model(wakeword_models=[model], inference_framework="onnx", **kw)


def speech_threshold(noise_levels, floor, factor=2.5):
    """Loudness that counts as speech: a multiple of the room's noise, never below the configured floor."""
    if not noise_levels:
        return floor
    # 20th percentile: the quiet moments, even if you were talking for most of the window.
    return max(floor, round(float(np.percentile(noise_levels, 20)) * factor))


NOISE_FRAMES = 125  # ~10 s of room sound kept while waiting for the wake word
noise = []


def wait_for_wake(stream, model, threshold):
    model.reset()
    while True:
        f = read(stream)
        noise.append(rms(f))
        del noise[:-NOISE_FRAMES]
        if max(model.predict(f).values()) >= threshold:
            return


def record(stream, cfg, start_timeout_s=None):
    a = cfg["audio"]
    thr = speech_threshold(noise, a["rms_threshold"], a["noise_factor"])
    frames, levels = [], []
    while True:
        f = read(stream)
        frames.append(f)
        levels.append(rms(f))
        d = decide(levels, thr, a["silence_s"], a["max_s"], start_timeout_s or a["start_timeout_s"])
        if d == "abort":
            return None
        if d == "stop":
            return np.concatenate(frames)


def drain(stream):
    """Drop audio buffered while Zade was speaking, so it never hears itself (FR-4)."""
    if n := stream.read_available:
        stream.read(n)


def cue(stream):
    """Beep, then drop the beep from the mic buffer so it is not recorded as speech."""
    chime()
    drain(stream)


def chime():
    t = np.linspace(0, 0.12, int(RATE * 0.12), False)
    sd.play((0.2 * np.sin(2 * np.pi * 880 * t)).astype(np.float32), RATE)
    sd.wait()

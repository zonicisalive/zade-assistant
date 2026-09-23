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


def wait_for_wake(stream, model, threshold):
    model.reset()
    while max(model.predict(read(stream)).values()) < threshold:
        pass


def record(stream, cfg, start_timeout_s=None):
    a = cfg["audio"]
    frames, levels = [], []
    while True:
        f = read(stream)
        frames.append(f)
        levels.append(rms(f))
        d = decide(levels, a["rms_threshold"], a["silence_s"], a["max_s"], start_timeout_s or a["start_timeout_s"])
        if d == "abort":
            return None
        if d == "stop":
            return np.concatenate(frames)


def drain(stream):
    """Drop audio buffered while Zade was speaking, so it never hears itself (FR-4)."""
    if n := stream.read_available:
        stream.read(n)


def chime():
    t = np.linspace(0, 0.12, int(RATE * 0.12), False)
    sd.play((0.2 * np.sin(2 * np.pi * 880 * t)).astype(np.float32), RATE)
    sd.wait()

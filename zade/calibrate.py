"""Microphone calibration: measure the room, then the user's voice, and pick audio.noise_factor.

Zade treats sound above (quiet-room level x noise_factor) as speech. Too low and background noise (TV,
people) counts as talking; too high and quiet words are missed. Used by the app (python -m zade.ctl
calibrate quiet|voice).
"""

import json
import pathlib

import numpy as np

from . import audio

NOISE_SECONDS, VOICE_SECONDS = 5, 6
FACTORS = [x / 4 for x in range(8, 25)]  # 2.0 ... 6.0 in steps of 0.25


def levels(clip):
    return np.array([audio.rms(clip[i:i + audio.FRAME]) for i in range(0, len(clip) - audio.FRAME + 1, audio.FRAME)])


def speech_levels(clip):
    """Frame levels inside the parts Silero hears as speech."""
    from faster_whisper.vad import get_speech_timestamps

    lv = levels(clip)
    mask = np.zeros(len(lv), bool)
    for t in get_speech_timestamps(clip.astype(np.float32) / 32768):
        mask[t["start"] // audio.FRAME:t["end"] // audio.FRAME + 1] = True
    return lv[mask[:len(lv)]]


def recommend(noise, voice, floor=500, max_noise=0.03):
    """The lowest noise_factor that keeps room noise under `max_noise` of the time, without putting the
    threshold above the user's normal voice level (then the room is too loud: use push-to-talk)."""
    base = float(np.percentile(noise, 20))  # what audio.speech_threshold uses as the room level
    voice_mid = float(np.median(voice))
    ceiling = 0.8 * float(np.percentile(voice, 75))  # the stressed part of normal speech must clear it
    best = None
    for f in FACTORS:
        thr = max(floor, base * f)
        if thr > ceiling:
            break
        best = f
        if (noise > thr).mean() <= max_noise:
            break
    factor = best or FACTORS[0]
    thr = max(floor, base * factor)
    return {"noise_factor": factor, "threshold": round(thr),
            "noise_pct": round(100 * float((noise > thr).mean())), "voice_pct": round(100 * float((voice > thr).mean())),
            "room": round(float(np.median(noise))), "voice": round(voice_mid),
            "too_noisy": bool((noise > thr).mean() > max_noise)}


def _record(seconds):
    import sounddevice as sd

    clip = sd.rec(int(seconds * audio.RATE), samplerate=audio.RATE, channels=1, dtype="int16")
    sd.wait()
    return clip[:, 0]


def run(step, data_dir):
    """step "quiet": measure the room; step "voice": measure the voice and recommend a setting."""
    saved = pathlib.Path(data_dir) / "calibration_noise.json"
    if step == "quiet":
        noise = levels(_record(NOISE_SECONDS))
        saved.write_text(json.dumps(noise.tolist()))
        return {"ok": True, "room": round(float(np.median(noise)))}
    if not saved.exists():
        return {"ok": False, "error": "Measure the quiet room first."}
    voice = speech_levels(_record(VOICE_SECONDS))
    if len(voice) < 5:
        return {"ok": False, "error": "I didn't hear you speak. Try again, a bit louder."}
    return {"ok": True, **recommend(np.array(json.loads(saved.read_text())), voice)}

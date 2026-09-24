"""Microphone calibration: measure the room, then the user's voice, and pick audio.noise_factor.

Zade treats sound above (quiet-room level x noise_factor) as speech. Too low and background noise (TV,
people) counts as talking; too high and quiet words are missed. Used by the app (python -m zade.ctl
calibrate quiet|voice).
"""

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


def gate(clip, thr, hold_frames=3):
    """The clip with everything Zade treats as silence muted: what counts as talking at this threshold.
    A short hold keeps word endings from sounding choppy."""
    out = np.zeros_like(clip)
    keep = 0
    for i in range(0, len(clip) - audio.FRAME + 1, audio.FRAME):
        frame = clip[i:i + audio.FRAME]
        keep = hold_frames if audio.rms(frame) > thr else keep - 1
        if keep > 0:
            out[i:i + audio.FRAME] = frame
    return out


def _record(seconds):
    import sounddevice as sd

    clip = sd.rec(int(seconds * audio.RATE), samplerate=audio.RATE, channels=1, dtype="int16")
    sd.wait()
    return clip[:, 0]


def _paths(data_dir):
    d = pathlib.Path(data_dir)
    return d / "calibration_room.npy", d / "calibration_voice.npy"


def stats(data_dir, factor=None):
    """Numbers for the saved recordings: the recommendation, or the result of a given noise_factor."""
    room_path, voice_path = _paths(data_dir)
    room, voice = np.load(room_path), np.load(voice_path)
    r = recommend(levels(room), speech_levels(voice))
    if factor is not None:
        noise, speech = levels(room), speech_levels(voice)
        thr = max(500, float(np.percentile(noise, 20)) * factor)
        r.update(noise_factor=factor, threshold=round(thr), noise_pct=round(100 * float((noise > thr).mean())),
                 voice_pct=round(100 * float((speech > thr).mean())))
    return r


def play(data_dir, which, factor=None):
    """Play the room + voice recording as heard ("before") or as Zade separates it ("after")."""
    import sounddevice as sd

    room_path, voice_path = _paths(data_dir)
    clip = np.concatenate([np.load(room_path), np.load(voice_path)])
    if which == "after":
        clip = gate(clip, stats(data_dir, factor)["threshold"])
    sd.play(clip, audio.RATE)
    sd.wait()
    return {"ok": True}


def run(step, data_dir):
    """step "quiet": measure the room; step "voice": measure the voice and recommend a setting."""
    room_path, voice_path = _paths(data_dir)
    if step == "quiet":
        room = _record(NOISE_SECONDS)
        np.save(room_path, room)
        return {"ok": True, "room": round(float(np.median(levels(room))))}
    if not room_path.exists():
        return {"ok": False, "error": "Measure the quiet room first."}
    voice = _record(VOICE_SECONDS)
    if len(speech_levels(voice)) < 5:
        return {"ok": False, "error": "I didn't hear you speak. Try again, a bit louder."}
    np.save(voice_path, voice)
    return {"ok": True, **stats(data_dir)}

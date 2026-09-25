"""Synthetic Indian-accented "hey Zade" clips for wake word training.

    uv run --with edge-tts python scripts/gen_indian_tts.py [out.zip] [kokoro_clips]

Microsoft Edge's Indian English and Hindi neural voices (every speed and pitch combination) plus Kokoro's
Hindi voices blended with its English ones at random (many distinct Indian-accented speakers). Every clip
is trimmed to the speech and kept only if Whisper hears "hey Zade" in it. The zip has a synthetic/ folder,
which train_wake.py adds as extra wake-word examples without diluting the real recordings.
"""

import asyncio
import io
import pathlib
import random
import subprocess
import sys
import tempfile
import wave
import zipfile

import numpy as np

from zade import stt

RATE = 16000
EDGE_VOICES = ["en-IN-NeerjaNeural", "en-IN-NeerjaExpressiveNeural", "en-IN-PrabhatNeural",
               "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"]
EDGE_TEXTS = ["Hey Zade", "Hey, Zade!"]
HINDI_TEXT = "हे ज़ेड"
KOKORO_DIR = pathlib.Path("~/.local/share/zade/kokoro").expanduser()


def trim(a, pad=0.15):
    """The spoken part, with a little silence either side."""
    frames = np.abs(a.astype(np.float32))
    loud = np.where(frames > max(300, 0.08 * frames.max()))[0]
    if not len(loud):
        return None
    p = int(pad * RATE)
    return a[max(0, loud[0] - p):loud[-1] + p]


def wav_bytes(a):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.astype(np.int16).tobytes())
    return buf.getvalue()


async def edge_clips(tmp, limit=8):
    import edge_tts

    jobs = [(v, t if v.startswith("en") else HINDI_TEXT if t == "Hey Zade" else t, r, p)
            for v in EDGE_VOICES for t in EDGE_TEXTS
            for r in range(-30, 31, 5) for p in range(-20, 21, 5)]
    sem = asyncio.Semaphore(limit)
    out = []

    async def one(i, voice, text, rate, pitch):
        mp3 = tmp / f"edge_{i}.mp3"
        async with sem:
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz").save(str(mp3))
                    break
                except Exception:
                    await asyncio.sleep(2 + attempt * 3)
            else:
                return
        pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(RATE),
                              "-f", "s16le", "-"], capture_output=True).stdout
        mp3.unlink(missing_ok=True)
        out.append((f"edge_{voice}_{rate}_{pitch}_{i}", np.frombuffer(pcm, np.int16)))

    await asyncio.gather(*(one(i, *j) for i, j in enumerate(jobs)))
    print(f"edge: {len(out)} of {len(jobs)} clips", flush=True)
    return out


def kokoro_clips(n, seed=7):
    from kokoro_onnx import Kokoro

    k = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    hindi = ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"]
    english = [v for v in k.get_voices() if v[:2] in ("af", "am", "bf", "bm")]
    rng = random.Random(seed)
    out = []
    for i in range(n):
        h, e, w = rng.choice(hindi), rng.choice(english), rng.uniform(0.55, 0.9)
        style = w * k.get_voice_style(h) + (1 - w) * k.get_voice_style(e)
        text, lang = (HINDI_TEXT, "hi") if rng.random() < 0.6 else (rng.choice(["hey Zade", "hey, Zade"]), "en-us")
        a, sr = k.create(text, voice=style, speed=rng.uniform(0.8, 1.3), lang=lang)
        a16 = (np.interp(np.arange(0, len(a), sr / RATE), np.arange(len(a)), a) * 32767).astype(np.int16)
        out.append((f"kokoro_{h}_{e}_{w:.2f}_{i}", a16))
        if (i + 1) % 500 == 0:
            print(f"kokoro: {i + 1}/{n}", flush=True)
    return out


def main(argv):
    dest = pathlib.Path(argv[0] if argv else "~/.local/share/zade/kaggle_upload/synthetic_indian.zip").expanduser()
    n_kokoro = int(argv[1]) if len(argv) > 1 else 3000
    with tempfile.TemporaryDirectory() as tmp:
        clips = asyncio.run(edge_clips(pathlib.Path(tmp))) + kokoro_clips(n_kokoro)
    kept = {"edge": 0, "kokoro": 0}
    whisper = stt._whisper("base.en", "cpu")
    with zipfile.ZipFile(dest, "w") as z:
        for name, a in clips:
            a = trim(a)
            if a is None or not 0.4 * RATE <= len(a) <= 2.5 * RATE:
                continue
            segs, _ = whisper.transcribe(a.astype(np.float32) / 32768, language="en", beam_size=1,
                                         hotwords="Zade, hey Zade", vad_filter=False)
            if stt.heard_wake_word(" ".join(s.text for s in segs)):
                z.writestr(f"synthetic/{name}.wav", wav_bytes(a))
                kept[name.split("_")[0]] += 1
    print(f"kept {kept} of {len(clips)} -> {dest}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])

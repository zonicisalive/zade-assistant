# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy", "sounddevice"]
# ///
"""Record "hey Zade" clips to help train Zade's wake word. Works on Windows, macOS and Linux.

    uv run record_voice.py            (installs what it needs by itself)
or  pip install numpy sounddevice  then  python record_voice.py

You say "hey Zade" in different ways, then a few other phrases. It makes voice_<name>.zip next to
this script: send that file back.
"""

import io
import pathlib
import re
import sys
import wave
import zipfile

import numpy as np
import sounddevice as sd

RATE, FRAME = 16000, 1280
STYLES = ["normally", "a bit faster", "slowly", "quietly", "loudly", "from further away (lean back)",
          "tired", "excited", "like you're busy", "like a question"]
NEAR_MISSES = ["hey Jade", "hey Kate", "hey there", "hey Siri", "hey say", "okay", "hey made it",
               "hey wait", "pay day", "hazy", "Zayn", "hey stay", "hey they said", "hey what's up",
               "open Firefox", "what time is it", "play some music", "hey Google", "hey Dave", "the day"]


def trim(clip, floor=500, pad_s=0.2):
    """The spoken part of a clip, with a little padding; None if nothing was said."""
    frames = [clip[i:i + FRAME] for i in range(0, len(clip) - FRAME + 1, FRAME)]
    levels = [float(np.sqrt(np.mean(f.astype(np.float32) ** 2))) for f in frames]
    if not levels:
        return None
    thr = max(floor, 4 * float(np.percentile(levels, 20)))
    loud = [i for i, level in enumerate(levels) if level > thr]
    if not loud:
        return None
    pad = int(pad_s * RATE)
    return clip[max(0, loud[0] * FRAME - pad):min(len(clip), (loud[-1] + 1) * FRAME + pad)]


def wav_bytes(clip):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(clip.astype(np.int16).tobytes())
    return buf.getvalue()


def session(z, kind, count, prompts, tag):
    start = sum(1 for x in z.namelist() if x.startswith(kind + "/"))  # recording again adds more
    n = 0
    while n < count:
        input(f"\n[{kind} {n + 1}/{count}] Say: {prompts[n % len(prompts)]}\n  Press Enter, then speak... ")
        clip = sd.rec(int(2.5 * RATE), samplerate=RATE, channels=1, dtype="int16")
        sd.wait()
        clip = trim(clip[:, 0])
        if clip is None or len(clip) < 0.4 * RATE:
            print("  Didn't catch that, try again.")
            continue
        z.writestr(f"{kind}/{tag}_{start + n:04d}.wav", wav_bytes(clip))
        print(f"  Saved ({len(clip) / RATE:.1f} s).")
        n += 1


def main(argv):
    positives = int(argv[0]) if argv else 60
    negatives = int(argv[1]) if len(argv) > 1 else 20
    name = re.sub(r"[^a-z0-9]+", "_", input("Your first name: ").strip().lower()) or "friend"
    out = pathlib.Path(__file__).resolve().parent / f"voice_{name}.zip"
    print(f"\n{positives} \"hey Zade\" clips, then {negatives} other phrases. \"Zade\" rhymes with \"made\".")
    print("Use a quiet-ish room (a fan is fine; no TV or music). Ctrl+C stops; saved clips are kept.")
    with zipfile.ZipFile(out, "a") as z:
        try:
            session(z, "positive", positives, [f"hey Zade  ({s})" for s in STYLES], name)
            session(z, "negative", negatives, NEAR_MISSES, name)
        except KeyboardInterrupt:
            print("\nStopped.")
    print(f"\nThanks! Send this file back: {out}")


if __name__ == "__main__":
    main(sys.argv[1:])

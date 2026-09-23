"""Which Whisper model understands YOUR voice best? Records you reading sentences, then scores each model.

Usage: uv run python scripts/stt_bench.py            (record, then compare)
       uv run python scripts/stt_bench.py --reuse    (compare again on the saved recordings)
"""
import difflib
import pathlib
import re
import sys
import time
import wave

import numpy as np

from zade import audio, config, stt

SENTENCES = [
    "open firefox",
    "go to workspace two",
    "set a timer for five minutes",
    "what's the weather in Mumbai tomorrow",
    "remind me in ten minutes to call my mother",
    "increase the volume to sixty percent",
    "when I say gaming mode open steam and discord",
    "what is the difference between TCP and UDP",
]
MODELS = ["base.en", "small.en", "distil-small.en", "large-v3-turbo"]
OUT = pathlib.Path("~/.local/share/zade/stt_bench").expanduser()


def words(text):
    text = text.lower().replace("%", " percent")
    for n, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]):
        text = re.sub(rf"\b{n}\b", w, text)
    text = re.sub(r"\b60\b", "sixty", text)
    return re.sub(r"[^a-z' ]", " ", text).split()


def accuracy(said, heard):
    return difflib.SequenceMatcher(None, words(said), words(heard)).ratio()


def record_all(cfg):
    OUT.mkdir(parents=True, exist_ok=True)
    stream = audio.open_stream()
    print("Measuring room noise, stay quiet for 2 seconds...")
    for _ in range(25):
        audio.noise.append(audio.rms(audio.read(stream)))
    for i, s in enumerate(SENTENCES):
        input(f"\n[{i + 1}/{len(SENTENCES)}] Press Enter, wait for the beep, then say:\n    \"{s}\"")
        audio.drain(stream)
        audio.cue(stream)
        a = audio.record(stream, cfg, start_timeout_s=6)
        if a is None:
            print("  (heard nothing, skipped)")
            continue
        with wave.open(str(OUT / f"{i}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(audio.RATE)
            w.writeframes(a.tobytes())
        print(f"  recorded {len(a) / audio.RATE:.1f}s")


def load(i):
    with wave.open(str(OUT / f"{i}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def main():
    cfg = config.load()
    cfg["stt"]["provider"] = "whisper"
    if "--reuse" not in sys.argv:
        record_all(cfg)
    clips = [(i, s, load(i)) for i, s in enumerate(SENTENCES) if (OUT / f"{i}.wav").exists()]
    print(f"\nComparing {len(MODELS)} models on {len(clips)} recordings (first run downloads the models)...\n")
    summary = []
    for m in MODELS:
        cfg["stt"]["model"] = m
        stt.transcribe(clips[0][2], cfg)  # load/download before timing
        scores, times = [], []
        print(f"== {m}")
        for i, said, a in clips:
            t = time.perf_counter()
            heard = stt.transcribe(a, cfg)
            times.append(time.perf_counter() - t)
            scores.append(accuracy(said, heard))
            print(f"  {scores[-1] * 100:5.0f}%  {times[-1] * 1000:5.0f} ms  heard: {heard!r}")
        summary.append((m, np.mean(scores), np.median(times)))
    print("\nModel             accuracy   typical time")
    for m, acc, t in summary:
        print(f"{m:17} {acc * 100:6.0f}%   {t * 1000:6.0f} ms")


if __name__ == "__main__":
    main()

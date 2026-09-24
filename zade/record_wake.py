"""Record your own "hey Zade" clips (and near-miss phrases) for wake word training.

    python -m zade.record_wake [positives] [negatives]

Clips are trimmed to the speech, saved as 16 kHz mono WAV under ~/.local/share/zade/wake_samples/,
and zipped with the training script into ~/.local/share/zade/kaggle_upload/ for Kaggle. Zade is put in Do Not
Disturb while recording, so it doesn't wake on every "hey Zade".
"""

import pathlib
import shutil
import sys
import wave

import numpy as np

from . import audio, config, ctl

OUT = pathlib.Path(config.DEFAULTS["paths"]["data"]).expanduser() / "wake_samples"
STYLES = ["normally", "a bit faster", "slowly", "quietly", "loudly", "from further away (lean back)",
          "tired", "excited", "like you're busy", "like a question"]
NEAR_MISSES = ["hey Jade", "hey Kate", "hey there", "hey Siri", "hey say", "okay", "hey made it",
               "hey wait", "pay day", "hazy", "Zayn", "hey stay", "hey they said", "hey what's up",
               "open Firefox", "what time is it", "play some music", "hey Google", "hey Dave", "the day"]
PAD_S = 0.2


def trim(clip, floor=500):
    """The spoken part of a clip, with a little padding; None if nothing was said."""
    frames = [clip[i:i + audio.FRAME] for i in range(0, len(clip) - audio.FRAME + 1, audio.FRAME)]
    levels = [audio.rms(f) for f in frames]
    if not levels:
        return None
    thr = max(floor, 4 * float(np.percentile(levels, 20)))
    loud = [i for i, level in enumerate(levels) if level > thr]
    if not loud:
        return None
    pad = int(PAD_S * audio.RATE)
    start = max(0, loud[0] * audio.FRAME - pad)
    end = min(len(clip), (loud[-1] + 1) * audio.FRAME + pad)
    return clip[start:end]


def _save(path, clip):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.RATE)
        w.writeframes(clip.astype(np.int16).tobytes())


def _take(stream, prompt, seconds=2.5):
    input(f"\n{prompt}\n  Press Enter, then speak... ")
    audio.drain(stream)
    frames = [audio.read(stream) for _ in range(round(seconds / audio.FRAME_S))]
    return trim(np.concatenate(frames))


def _session(kind, count, prompts):
    folder = OUT / kind
    folder.mkdir(parents=True, exist_ok=True)
    done = len(list(folder.glob("*.wav")))
    stream = audio.open_stream()
    try:
        n = 0
        while n < count:
            clip = _take(stream, f"[{kind} {n + 1}/{count}] Say: {prompts[n % len(prompts)]}")
            if clip is None or len(clip) < 0.4 * audio.RATE:
                print("  Didn't catch that, try again.")
                continue
            _save(folder / f"{kind}_{done + n:04d}.wav", clip)
            print(f"  Saved ({len(clip) / audio.RATE:.1f} s).")
            n += 1
    finally:
        stream.close()


def main(argv):
    positives = int(argv[0]) if argv else 60
    negatives = int(argv[1]) if len(argv) > 1 else 20
    print(f"Recording {positives} \"hey Zade\" clips and {negatives} other phrases into {OUT}.")
    print("Speak in your normal voice and vary it as asked. Ctrl+C stops; saved clips are kept.")
    was_dnd = bool(config.load(ctl.CONFIG)["quiet"]["dnd"])
    ctl.set_setting("quiet.dnd", "true")  # Zade must not wake on every "hey Zade"
    try:
        _session("positive", positives, [f"hey Zade  ({s})" for s in STYLES])
        _session("negative", negatives, NEAR_MISSES)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ctl.set_setting("quiet.dnd", str(was_dnd).lower())
    upload = OUT.parent / "kaggle_upload"  # everything the Kaggle dataset needs, in one folder
    upload.mkdir(exist_ok=True)
    shutil.make_archive(str(upload / "wake_samples"), "zip", OUT)
    shutil.copy(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "train_wake.py", upload)
    print(f"\nDone. Upload both files in {upload} as a Kaggle dataset (see scripts/train_wake.py).")


if __name__ == "__main__":
    main(sys.argv[1:])

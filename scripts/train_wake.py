"""Train Zade's "hey Zade" wake word model (openWakeWord) on Kaggle, or any Linux machine with internet.

It builds its own Python 3.11 environment with pinned packages (hosted notebooks keep changing their
Python and numpy versions, which broke the official Colab notebook), downloads the training data,
generates synthetic "hey Zade" clips, mixes in your own recordings from `python -m zade.record_wake`,
and trains. The result is hey_zade.onnx: copy it to ~/.local/share/zade/zade.onnx.

Kaggle (free GPU):
  1. Datasets -> New Dataset: upload this file, wake_samples.zip and any friends' voice_<name>.zip
     (from scripts/record_voice.py). Keep it private. Name it zade-wake.
  2. Code -> New Notebook. Settings: Accelerator "GPU T4 x2", Internet on.
     Add Input -> your zade-wake dataset.
  3. One cell:  !python /kaggle/input/zade-wake/train_wake.py
  4. Save Version -> "Save & Run All (Commit)". It keeps running with the tab closed (up to 12 h).
     When it finishes, the version's Output tab has hey_zade.onnx.

Options: --samples 50000 --steps 50000 --penalty 1500 --audioset-parts 3 --acav-gb 17
"""

import argparse
import glob
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile

PHRASE = "hey zayd"   # spelled the way the TTS voices say "Zade" best
MODEL_NAME = "hey_zade"
PINS = ["numpy==1.26.4", "scipy==1.11.4", "torch==2.5.1", "torchaudio==2.5.1", "piper-phonemize==1.1.0",
        "webrtcvad-wheels==2.0.14", "mutagen==1.47.0", "torchinfo==1.8.0", "torchmetrics==1.2.0",
        "speechbrain==0.5.16", "audiomentations==0.33.0", "torch-audiomentations==0.11.0",
        "acoustics==0.2.6", "pronouncing==0.2.0", "onnxruntime==1.18.1", "onnx==1.16.2",
        "scikit-learn==1.5.2", "soundfile==0.12.1", "pyarrow==17.0.0", "pyyaml", "tqdm", "requests"]
PIPER_TAG = "v2.0.0"  # newer releases changed layout, and openWakeWord's train.py imports the old one
HF = "https://huggingface.co"
OWW_MODELS = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
# Phrases that sound close to the wake word: the model learns NOT to wake on these.
NEGATIVE_PHRASES = [
    "hey jade", "hey made", "hey wade", "hey shade", "hey fade", "hey kate", "hey nate",
    "hey dave", "hey zack", "hey sadie", "hey say", "hey they", "hey there", "hey day",
    "hey stay", "hey play", "hey maid", "hey trade", "hey zane", "hey zayn", "hey jay",
    "hey its late", "hey wait", "hey great", "zade", "hey", "hey you", "okay",
    "hey google", "hey siri", "alexa", "hey jarvis", "made it", "pay day", "hazy days",
]


def sh(*cmd, **kw):
    print("RUN:", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def download(url, path, headers=None):
    path = pathlib.Path(path)
    if path.exists() and path.stat().st_size > 0:
        return path
    print("GET:", url, flush=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.rename(path)
    return path


# ── Stage 1 (any Python): build the pinned environment, then re-run this file inside it ──────────────
def bootstrap(args, argv):
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    py = work / "venv" / "bin" / "python"
    if not (work / "venv" / ".ready").exists():
        if shutil.which("uv"):
            uv = [shutil.which("uv")]
        else:
            sh(sys.executable, "-m", "pip", "install", "-q", "uv")
            uv = [sys.executable, "-m", "uv"]
        sh(*uv, "venv", "--python", "3.11", work / "venv")
        sh(*uv, "pip", "install", "--python", py, *PINS)
        if not (work / "piper-sample-generator").exists():
            sh("git", "clone", "--depth", "1", "--branch", PIPER_TAG,
               "https://github.com/rhasspy/piper-sample-generator", work / "piper-sample-generator")
        if not (work / "openwakeword").exists():
            sh("git", "clone", "--depth", "1", "https://github.com/dscripka/openwakeword", work / "openwakeword")
        sh(*uv, "pip", "install", "--python", py, "--no-deps", "-e", work / "openwakeword")
        (work / "venv" / ".ready").touch()
    os.execv(str(py), [str(py), __file__, "--inner", *argv])


# ── Stage 2 (pinned environment): data, clips, training ─────────────────────────────────────────────
def get_models(work):
    download(f"https://github.com/rhasspy/piper-sample-generator/releases/download/{PIPER_TAG}/en_US-libritts_r-medium.pt",
             work / "piper-sample-generator" / "models" / "en_US-libritts_r-medium.pt")
    res = work / "openwakeword" / "openwakeword" / "resources" / "models"
    res.mkdir(parents=True, exist_ok=True)
    for name in ["embedding_model.onnx", "melspectrogram.onnx"]:
        download(OWW_MODELS + name, res / name)
    # DeepPhonemizer's model link is dead; it only invents extra look-alike phrases, so skip it
    # (NEGATIVE_PHRASES lists them by hand).
    data_py = work / "openwakeword" / "openwakeword" / "data.py"
    src = data_py.read_text()
    if "_orig_generate_adversarial_texts" not in src:
        data_py.write_text(src.replace("def generate_adversarial_texts(",
                                       "def generate_adversarial_texts(*args, **kwargs):\n    return []\n\n"
                                       "def _orig_generate_adversarial_texts(", 1))


def hf_files(repo, folder):
    with urllib.request.urlopen(f"{HF}/api/datasets/{repo}/tree/main/{folder}", timeout=60) as r:
        return [x["path"] for x in json.load(r) if x["type"] == "file"]


def get_rirs(work):
    out = work / "mit_rirs"
    out.mkdir(exist_ok=True)
    for path in hf_files("davidscripka/MIT_environmental_impulse_responses", "16khz"):
        download(f"{HF}/datasets/davidscripka/MIT_environmental_impulse_responses/resolve/main/{path}",
                 out / pathlib.Path(path).name)
    return out


def get_background(work, parts, max_clips):
    """AudioSet clips (speech, music, noise), converted to 16 kHz mono WAV."""
    import numpy as np
    import pyarrow.parquet as pq
    import scipy.io.wavfile
    import scipy.signal
    import soundfile

    out = work / "audioset_16k"
    out.mkdir(exist_ok=True)
    if len(list(out.glob("*.wav"))) >= max_clips:
        return out
    written = len(list(out.glob("*.wav")))
    for path in sorted(hf_files("agkphysics/AudioSet", "data/bal_train"))[:parts]:
        local = download(f"{HF}/datasets/agkphysics/AudioSet/resolve/main/{path}", work / pathlib.Path(path).name)
        table = pq.read_table(local, columns=["video_id", "audio"])
        for vid, a in zip(table.column("video_id").to_pylist(), table.column("audio").to_pylist()):
            if written >= max_clips:
                break
            try:
                data, sr = soundfile.read(io.BytesIO(a["bytes"]), dtype="float32")
            except Exception:
                continue
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != 16000:
                data = scipy.signal.resample_poly(data, 16000, sr)
            scipy.io.wavfile.write(out / f"{vid}.wav", 16000, (np.clip(data, -1, 1) * 32767).astype(np.int16))
            written += 1
        local.unlink()  # the parquet is ~700 MB; the WAVs are what training needs
    print(f"background clips: {written}", flush=True)
    return out


def get_features(work, acav_gb):
    """Pre-computed negative features (~2,000 h of ACAV100M speech/noise), optionally only the first part."""
    import numpy as np

    url = f"{HF}/datasets/davidscripka/openwakeword_features/resolve/main/"
    val = download(url + "validation_set_features.npy", work / "validation_set_features.npy")
    out = work / "acav_features.npy"
    if not out.exists():
        with urllib.request.urlopen(urllib.request.Request(url + "openwakeword_features_ACAV100M_2000_hrs_16bit.npy",
                                                           headers={"Range": "bytes=0-4095"}), timeout=60) as r:
            head = r.read()
        header_len = int.from_bytes(head[8:10], "little")
        offset = 10 + header_len
        meta = eval(head[10:offset].decode("latin1"))  # the .npy header is a Python dict literal
        rows_total, *frame = meta["shape"]
        row_bytes = int(np.prod(frame)) * np.dtype(meta["descr"]).itemsize
        rows = min(rows_total, int(acav_gb * 1e9 // row_bytes))
        part = out.with_suffix(".part")
        with open(part, "wb") as f:
            np.lib.format.write_array_header_1_0(f, {"descr": meta["descr"], "fortran_order": False,
                                                     "shape": (rows, *frame)})
            req = urllib.request.Request(url + "openwakeword_features_ACAV100M_2000_hrs_16bit.npy",
                                         headers={"Range": f"bytes={offset}-{offset + rows * row_bytes - 1}"})
            print(f"GET: ACAV100M features, {rows}/{rows_total} rows ({rows * row_bytes / 1e9:.1f} GB)", flush=True)
            with urllib.request.urlopen(req, timeout=120) as r:
                shutil.copyfileobj(r, f, 1 << 20)
        part.rename(out)
    return out, val


def find_recordings(given):
    """Every recordings zip: yours (wake_samples.zip) and friends' (voice_<name>.zip)."""
    found = given.split(",") if given else glob.glob("/kaggle/input/**/*.zip", recursive=True)
    return [pathlib.Path(p) for p in found if p]


def add_recordings(zips, clips_dir, n_samples):
    """Mix real voices into the generated ones: repeated so they make up ~5% of the positives
    (augmentation adds different noise and echo to every copy), and near-misses as negatives."""
    pos, neg = [], []
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                if name.endswith(".wav") and name.split("/")[0] in ("positive", "negative"):
                    (pos if name.startswith("positive/") else neg).append(z.read(name))
        print(f"recordings from {zp.name}", flush=True)
    if not pos and not neg:
        print("No recordings found: training on synthetic voices only.", flush=True)
        return
    for kind, clips, copies in [("positive_train", pos, max(1, n_samples // 20 // max(1, len(pos)))),
                                ("negative_train", neg, 10)]:
        for i, data in enumerate(clips):
            for c in range(copies):
                (clips_dir / kind / f"real_{i:05d}_{c:03d}.wav").write_bytes(data)
        print(f"added {len(clips)} real clips x{copies} to {kind}", flush=True)


def inner(args):
    import torch
    import yaml

    work = pathlib.Path(args.work)
    os.chdir(work)
    print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU, slower)", flush=True)
    free_gb = shutil.disk_usage(work).free / 1e9
    acav_gb = min(args.acav_gb, max(1.0, free_gb - 25))  # leave room for clips and features
    print(f"disk free: {free_gb:.0f} GB, using {acav_gb:.1f} GB of ACAV100M features", flush=True)

    get_models(work)
    rirs = get_rirs(work)
    background = get_background(work, args.audioset_parts, args.max_background)
    acav, val = get_features(work, acav_gb)

    config = yaml.safe_load((work / "openwakeword" / "examples" / "custom_model.yml").read_text())
    config.update({
        "target_phrase": [PHRASE], "model_name": MODEL_NAME, "custom_negative_phrases": NEGATIVE_PHRASES,
        "n_samples": args.samples, "n_samples_val": max(min(1000, args.samples), args.samples // 10),
        "steps": args.steps, "max_negative_weight": args.penalty,
        "target_accuracy": 0.5, "target_recall": 0.25,
        "piper_sample_generator_path": str(work / "piper-sample-generator"),
        "output_dir": str(work / "model"), "rir_paths": [str(rirs)],
        "background_paths": [str(background)], "background_paths_duplication_rate": [1],
        "false_positive_validation_data_path": str(val),
        "feature_data_files": {"ACAV100M_sample": str(acav)},
    })
    (work / "zade.yaml").write_text(yaml.safe_dump(config))

    train = [sys.executable, work / "openwakeword" / "openwakeword" / "train.py", "--training_config", work / "zade.yaml"]
    sh(*train, "--generate_clips")
    add_recordings(find_recordings(args.recordings), work / "model" / MODEL_NAME, args.samples)
    sh(*train, "--augment_clips")
    sh(*train, "--train_model")

    onnx = work / "model" / f"{MODEL_NAME}.onnx"
    dest = pathlib.Path("/kaggle/working") if pathlib.Path("/kaggle/working").exists() else work
    shutil.copy(onnx, dest / onnx.name)
    print(f"\nDONE: {dest / onnx.name}\nCopy it to ~/.local/share/zade/zade.onnx and restart Zade.", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--work", default="/tmp/zade_wake_training", help="scratch folder (needs ~40 GB)")
    p.add_argument("--samples", type=int, default=50000, help="synthetic 'hey Zade' clips (and as many look-alikes)")
    p.add_argument("--steps", type=int, default=50000, help="training steps")
    p.add_argument("--penalty", type=int, default=1500, help="higher: fewer false wakes, more missed ones")
    p.add_argument("--audioset-parts", type=int, default=3, help="AudioSet background files, ~700 MB each")
    p.add_argument("--max-background", type=int, default=100000, help="cap on background clips")
    p.add_argument("--acav-gb", type=float, default=17.3, help="GB of pre-computed negative features (17.3 = all)")
    p.add_argument("--recordings", default="", help="recording zips, comma-separated (found automatically on Kaggle)")
    argv = sys.argv[1:]
    args = p.parse_args(argv)
    if args.inner:
        inner(args)
    else:
        bootstrap(args, [a for a in argv if a != "--inner"])


if __name__ == "__main__":
    main()

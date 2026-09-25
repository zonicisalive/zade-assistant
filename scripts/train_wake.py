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
  3. One cell:  !python $(find /kaggle/input -name train_wake.py | head -1)
  4. Save Version -> "Save & Run All (Commit)". It keeps running with the tab closed (up to 12 h).
     When it finishes, the version's Output tab has hey_zade.onnx.

Options: --samples 50000 --steps 50000 --penalty 1500 --audioset-parts 3 --acav-gb 17
"""

import argparse
import http.client
import ast
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
        "scikit-learn==1.5.2", "soundfile==0.12.1", "pyarrow==17.0.0", "pyyaml", "tqdm", "requests",
        "setuptools==70.3.0"]  # pronouncing imports pkg_resources, which setuptools 81+ no longer ships
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


def fetch(url, dest, start=0, end=None, tries=30):
    """Stream url's bytes [start, end] into dest (appending), resuming after dropped connections: big
    Hugging Face downloads often break partway on hosted notebooks."""
    import time

    dest = pathlib.Path(dest)
    done = dest.stat().st_size if dest.exists() else 0
    want = None if end is None else end - start + 1
    for attempt in range(tries):
        if want is not None and done >= want:
            return
        rng = f"bytes={start + done}-" + ("" if end is None else str(end))
        req = urllib.request.Request(url, headers={"Range": rng} if (start or done or end is not None) else {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "ab") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
            if want is None or done >= want:
                return
        except (OSError, http.client.HTTPException) as e:  # IncompleteRead, timeouts, resets
            print(f"  download interrupted at {done / 1e9:.2f} GB ({e}); resuming", flush=True)
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"could not download {url}")


def download(url, path):
    path = pathlib.Path(path)
    if path.exists() and path.stat().st_size > 0:
        return path
    print("GET:", url, flush=True)
    tmp = path.with_suffix(path.suffix + ".part")
    fetch(url, tmp)
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
        sh(*uv, "pip", "install", "-q", "--python", py, *PINS)  # -q: progress bars flood the Kaggle log
        if not (work / "piper-sample-generator").exists():
            sh("git", "clone", "--depth", "1", "--branch", PIPER_TAG,
               "https://github.com/rhasspy/piper-sample-generator", work / "piper-sample-generator")
        if not (work / "openwakeword").exists():
            sh("git", "clone", "--depth", "1", "https://github.com/dscripka/openwakeword", work / "openwakeword")
        sh(*uv, "pip", "install", "--python", py, "--no-deps", "-e", work / "openwakeword")
        # Import everything training uses now, so a broken package fails in a minute, not after the
        # ~10 minutes of downloads.
        sh(py, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                     "import torch, torchaudio, torchmetrics, torchinfo, speechbrain, audiomentations, "
                     "torch_audiomentations, acoustics, pronouncing, mutagen, webrtcvad, piper_phonemize, "
                     "onnxruntime, yaml, pyarrow, soundfile, openwakeword.data, openwakeword.utils; "
                     "from generate_samples import generate_samples; print('imports OK')",
           work / "piper-sample-generator")  # main() already set MPLBACKEND=Agg
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
        meta = ast.literal_eval(head[10:offset].decode("latin1"))  # the .npy header is a Python dict literal
        rows_total, *frame = meta["shape"]
        row_bytes = int(np.prod(frame)) * np.dtype(meta["descr"]).itemsize
        rows = min(rows_total, int(acav_gb * 1e9 // row_bytes))
        header, body = out.with_suffix(".head"), out.with_suffix(".part")
        with open(header, "wb") as f:
            np.lib.format.write_array_header_1_0(f, {"descr": meta["descr"], "fortran_order": False,
                                                     "shape": (rows, *frame)})
        print(f"GET: ACAV100M features, {rows}/{rows_total} rows ({rows * row_bytes / 1e9:.1f} GB)", flush=True)
        fetch(url + "openwakeword_features_ACAV100M_2000_hrs_16bit.npy", body, offset, offset + rows * row_bytes - 1)
        with open(header, "ab") as f, open(body, "rb") as b:  # header + data = a valid .npy
            shutil.copyfileobj(b, f, 1 << 24)
        body.unlink()
        header.rename(out)
    return out, val


def find_recordings(given):
    """Every set of recordings: zips (wake_samples.zip, voice_<name>.zip) or the folders Kaggle unpacks
    them into (anything holding positive/ or negative/ WAVs)."""
    if given:
        return [pathlib.Path(p) for p in given.split(",") if p]
    root = pathlib.Path("/kaggle/input")
    if not root.exists():
        return []
    zips = sorted(root.rglob("*.zip"))
    folders = sorted({d.parent for d in root.rglob("*") if d.is_dir() and d.name in KINDS})
    return zips + folders


KINDS = ("positive", "negative", "synthetic")  # real wake words, real near-misses, Indian-accent TTS


def _clips(source):
    """(kind, wav bytes) from a zip or an unpacked folder."""
    if source.is_dir():
        for kind in KINDS:
            for f in sorted((source / kind).glob("*.wav")):
                yield kind, f.read_bytes()
        return
    with zipfile.ZipFile(source) as z:
        for name in z.namelist():
            if name.endswith(".wav") and name.split("/")[0] in KINDS:
                yield name.split("/")[0], z.read(name)


def add_recordings(sources, clips_dir, n_samples):
    """Mix real voices into the generated ones: repeated so they make up ~5% of the positives
    (augmentation adds different noise and echo to every copy), and near-misses as negatives."""
    got = {kind: [] for kind in KINDS}
    for src in sources:
        before = sum(map(len, got.values()))
        for kind, data in _clips(src):
            got[kind].append(data)
        print(f"recordings from {src.name}: {sum(map(len, got.values())) - before} clips", flush=True)
    pos, neg, synthetic = got["positive"], got["negative"], got["synthetic"]
    for i, data in enumerate(synthetic):  # extra accented voices, once each: they must not outweigh real ones
        (clips_dir / "positive_train" / f"synth_{i:05d}.wav").write_bytes(data)
    if synthetic:
        print(f"added {len(synthetic)} Indian-accent synthetic clips to positive_train", flush=True)
    if not pos and not neg:
        print("No recordings found: training on synthetic voices only.", flush=True)
        return
    for kind, clips, copies in [("positive_train", pos, max(1, n_samples // 20 // max(1, len(pos)))),
                                ("negative_train", neg, 10)]:
        for i, data in enumerate(clips):
            for c in range(copies):
                (clips_dir / kind / f"real_{i:05d}_{c:03d}.wav").write_bytes(data)
        print(f"added {len(clips)} real clips x{copies} to {kind}", flush=True)


GEN_CHILD = """
import json, os, sys, uuid
a = json.loads(sys.argv[1])
sys.path.insert(0, a["piper"])
from generate_samples import generate_samples
os.makedirs(a["out"], exist_ok=True)
generate_samples(text=a["text"], max_samples=a["n"], batch_size=a["batch"], noise_scales=[0.98],
                 noise_scale_ws=[0.98], length_scales=[0.75, 1.0, 1.25], output_dir=a["out"],
                 auto_reduce_batch_size=True, file_names=[uuid.uuid4().hex + ".wav" for _ in range(a["n"])])
"""


def generate_on_all_gpus(work, config, gpus):
    """Make the two big training sets with every GPU at once (Kaggle's T4 x2): one process per GPU,
    each writing its share. openWakeWord's own --generate_clips step then sees them done (it resumes
    from what exists) and only makes the small test sets. A failed share is simply made by that step."""
    clips = work / "model" / MODEL_NAME
    jobs = [(clips / "positive_train", config["target_phrase"], config["tts_batch_size"]),
            (clips / "negative_train", config["custom_negative_phrases"], max(1, config["tts_batch_size"] // 7))]
    for out, text, batch in jobs:
        n = config["n_samples"]
        shares = [n // gpus + (1 if i < n % gpus else 0) for i in range(gpus)]
        print(f"generating {n} clips into {out.name} on {gpus} GPUs", flush=True)
        procs = [subprocess.Popen([sys.executable, "-c", GEN_CHILD, json.dumps(
                     {"piper": str(work / "piper-sample-generator"), "out": str(out), "text": text,
                      "n": share, "batch": batch})], env={**os.environ, "CUDA_VISIBLE_DEVICES": str(i)})
                 for i, share in enumerate(shares)]
        codes = [p.wait() for p in procs]
        made = len(list(out.glob("*.wav"))) if out.exists() else 0
        print(f"  {out.name}: {made} clips (exit codes {codes})", flush=True)


def inner(args):
    import torch
    import yaml

    work = pathlib.Path(args.work)
    os.chdir(work)
    print("GPU:", f"{torch.cuda.device_count()} x {torch.cuda.get_device_name(0)}" if torch.cuda.is_available()
          else "none (CPU, slower)", flush=True)
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
    if torch.cuda.device_count() > 1:
        generate_on_all_gpus(work, config, torch.cuda.device_count())
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
    # Notebook settings leak into our own Python: Kaggle's Jupyter backend breaks matplotlib (imported by
    # torchmetrics), and a PYTHONPATH could mix in the notebook's Python 3.12 packages.
    os.environ["MPLBACKEND"] = "Agg"
    for var in ("PYTHONPATH", "PYTHONHOME"):
        os.environ.pop(var, None)
    os.environ.setdefault("TQDM_MININTERVAL", "30")  # progress bars every 30 s, not thousands of log lines
    if args.inner:
        inner(args)
    else:
        bootstrap(args, [a for a in argv if a != "--inner"])


if __name__ == "__main__":
    main()

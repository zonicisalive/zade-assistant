"""Voice focus: keep only the voice of whoever started talking, like a smart speaker locking onto its user.

A recording is split into speech pieces (Silero VAD). The first real piece is the speaker's voiceprint
(NVIDIA TitaNet-small speaker embeddings, via sherpa-onnx); later pieces spoken by a different voice, such
as someone talking across the room, are cut before transcription. With one microphone, two people talking
at the same moment can't be separated; this removes others before, between and after the user's words.
"""

import functools
import logging
import pathlib
import shutil
import urllib.request

import numpy as np

log = logging.getLogger("zade")

RATE = 16000
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "nemo_en_titanet_small.onnx")
MIN_JUDGE_S = 0.6  # shorter pieces carry too little voice to judge: they are kept
GAP_S = 0.2        # silence put between the kept pieces


@functools.cache
def _extractor(data_dir):
    import sherpa_onnx

    path = pathlib.Path(data_dir).expanduser() / "models" / "nemo_en_titanet_small.onnx"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading the voice focus model (40 MB)")
        part = path.with_suffix(".part")  # renamed only when complete: a dropped download never looks finished
        with urllib.request.urlopen(MODEL_URL, timeout=30) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        part.rename(path)
    return sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=2))


def embed(clip, data_dir):
    ext = _extractor(data_dir)
    s = ext.create_stream()
    s.accept_waveform(RATE, clip.astype(np.float32) / 32768)
    s.input_finished()
    e = np.array(ext.compute(s))
    n = np.linalg.norm(e)
    return e / n if n and np.isfinite(n) else None


def speech_pieces(audio):
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    ts = get_speech_timestamps(audio.astype(np.float32) / 32768, VadOptions(min_silence_duration_ms=300))
    return [(t["start"], t["end"]) for t in ts]


def profile_path(data_dir):
    return pathlib.Path(data_dir).expanduser() / "voice_profile.npy"


def enroll(clips, data_dir, embed=embed):
    """Save the owner's voiceprint: the average of their recordings' voiceprints (much steadier than any
    single short clip). Returns how many clips were usable."""
    es = [e for e in (embed(c, data_dir) for c in clips) if e is not None]
    if not es:
        return 0
    p = np.mean(es, 0)
    np.save(profile_path(data_dir), p / np.linalg.norm(p))
    return len(es)


def _profile(data_dir):
    path = profile_path(data_dir)
    return np.load(path) if path.exists() else None


def focus(audio, cfg, embed=embed, pieces=speech_pieces, profile=None):
    """The recording with other people's speech removed (unchanged if there's nothing to remove).
    If the owner (their saved voiceprint) speaks in any piece, every piece is judged against that voiceprint,
    so a TV or a friend talking first can't take their place; otherwise against whoever started talking."""
    a = cfg["audio"]
    th = a.get("focus_threshold", 0.25)
    spans = pieces(audio)
    if len(spans) < 2:
        return audio
    data_dir = cfg["paths"]["data"]
    voices = {s: embed(audio[s[0]:s[1]], data_dir) for s in spans if s[1] - s[0] >= MIN_JUDGE_S * RATE}
    owner = _profile(data_dir) if profile is None else profile
    if owner is not None and any(e is not None and float(e @ owner) >= th for e in voices.values()):
        ref, first = owner, None
    else:
        first = next(iter(voices), spans[0])
        ref = voices[first] if first in voices else embed(audio[first[0]:first[1]], data_dir)
    if ref is None:
        return audio
    kept, dropped = [], 0
    for span in spans:
        e = voices.get(span)  # pieces too short to judge are kept
        if span != first and e is not None and float(e @ ref) < th:
            dropped += 1
            continue
        kept.append(audio[span[0]:span[1]])
    if not dropped or not kept:
        return audio
    log.info("voice focus: dropped %d of %d speech pieces from another voice", dropped, len(spans))
    gap = np.zeros(int(GAP_S * RATE), audio.dtype)
    return np.concatenate([x for piece in kept for x in (piece, gap)][:-1])

import functools
import io
import re
import logging
import pathlib
import wave

import numpy as np

from . import providers

log = logging.getLogger("zade")


@functools.cache
def _whisper(model, device):
    from faster_whisper import WhisperModel

    try:  # from the local cache: skips an online update check (~0.4 s) and works offline
        return WhisperModel(model, device=device, compute_type="int8", local_files_only=True)
    except Exception:  # not downloaded yet (first run): fetch it
        return WhisperModel(model, device=device, compute_type="int8")


def to_wav(audio, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.astype(np.int16).tobytes())
    return buf.getvalue()


def transcribe(audio, cfg, prompt="", hotwords=()):
    s = cfg["stt"]
    if s["provider"] == "openai":
        pc = cfg["providers"]["openai"]
        try:
            kw = {"prompt": prompt} if prompt else {}
            r = providers._client("openai", cfg).audio.transcriptions.create(
                model=pc["stt_model"], file=("speech.wav", to_wav(audio)), **kw)
            return r.text.strip()
        except Exception as e:  # any cloud failure (network, key, API): fall back to the local engine
            log.warning("cloud STT failed, using local whisper: %s", e)
    words = ", ".join(dict.fromkeys([*s["hotwords"], *hotwords]))
    segments, _ = _whisper(s["model"], s["device"]).transcribe(
        audio.astype(np.float32) / 32768, language="en", beam_size=s["beam_size"], initial_prompt=prompt or None,
        hotwords=words or None, vad_filter=True)
    # Drop what Whisper invents from noise: likely-silent or very unsure segments.
    return " ".join(seg.text.strip() for seg in segments
                    if seg.no_speech_prob <= 0.6 and seg.avg_logprob >= -1.0).strip()


# How Whisper tends to spell "Zade" (it has never seen the name): all accepted as the wake word.
# Per wake word: what Whisper is primed with, and the spellings that count as hearing it.
WAKE_WORDS = {
    "zade": ("Zade, hey Zade", {"zade", "zayd", "zaid", "zayed", "sade", "jade", "zadie"}),
    "jarvis": ("Jarvis, hey Jarvis", {"jarvis", "jarvus", "jervis", "javis"}),
    "alexa": ("Alexa", {"alexa", "alexia", "alexis", "lexa"}),
    "mycroft": ("Mycroft, hey Mycroft", {"mycroft", "microft", "mycraft", "microsoft"}),
    "rhasspy": ("Rhasspy, hey Rhasspy", {"rhasspy", "raspy", "rhaspy", "raspi", "raspberry"}),
}


def wake_word(model):
    """The WAKE_WORDS key for a wake model name or path (a custom model counts as "zade")."""
    stem = pathlib.Path(model).stem.lower()
    return next((k for k in WAKE_WORDS if k in stem), "zade")


def heard_wake_word(text, word="zade"):
    return any(w in WAKE_WORDS[word][1] for w in re.findall(r"[a-z]+", text.lower()))


def wake_check(audio, cfg):
    """Second opinion on a wake: a tiny Whisper model must actually hear the wake word in the last ~2 s."""
    word = wake_word(cfg["wake"]["model"])
    segments, _ = _whisper(cfg["wake"]["verify_model"], "cpu").transcribe(
        audio.astype(np.float32) / 32768, language="en", beam_size=1, hotwords=WAKE_WORDS[word][0], vad_filter=False)
    text = " ".join(seg.text.strip() for seg in segments)
    log.info("wake check heard %r", text)
    return heard_wake_word(text, word)

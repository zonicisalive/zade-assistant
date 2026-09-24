import functools
import io
import logging
import wave

import numpy as np

from . import providers

log = logging.getLogger("zade")


@functools.cache
def _whisper(model, device):
    from faster_whisper import WhisperModel

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

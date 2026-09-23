import functools
import io
import logging
import wave

import numpy as np
import openai

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


def transcribe(audio, cfg, prompt=""):
    s = cfg["stt"]
    if s["provider"] == "openai":
        pc = cfg["providers"]["openai"]
        try:
            kw = {"prompt": prompt} if prompt else {}
            r = providers._client("openai", cfg).audio.transcriptions.create(
                model=pc["stt_model"], file=("speech.wav", to_wav(audio)), **kw)
            return r.text.strip()
        except (providers.ProviderError, openai.OpenAIError) as e:
            log.warning("cloud STT failed, using local whisper: %s", e)
    segments, _ = _whisper(s["model"], s["device"]).transcribe(
        audio.astype(np.float32) / 32768, language="en", beam_size=1, initial_prompt=prompt or None)
    return " ".join(seg.text.strip() for seg in segments).strip()

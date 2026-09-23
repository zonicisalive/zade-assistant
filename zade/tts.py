import functools
import logging
import pathlib

import numpy as np
import openai
import sounddevice as sd

from . import providers

log = logging.getLogger("zade")


@functools.cache
def _piper(voice, data):
    from piper import PiperVoice

    return PiperVoice.load(str(pathlib.Path(data).expanduser() / "voices" / f"{voice}.onnx"))


def _play(samples, rate):
    sd.play(samples, rate)
    sd.wait()


def speak(text, cfg):
    if not text:
        return
    t = cfg["tts"]
    if t["provider"] == "openai":
        pc = cfg["providers"]["openai"]
        try:
            pcm = providers._client("openai", cfg).audio.speech.create(
                model=pc["tts_model"], voice=pc["tts_voice"], input=text, response_format="pcm").read()
            _play(np.frombuffer(pcm, np.int16), 24000)
            return
        except (providers.ProviderError, openai.OpenAIError) as e:
            log.warning("cloud TTS failed, using piper: %s", e)
    voice = _piper(t["voice"], cfg["paths"]["data"])
    chunks = list(voice.synthesize(text))
    _play(np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate)

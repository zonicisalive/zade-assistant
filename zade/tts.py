import functools
import logging
import pathlib
import re
import time
import unicodedata

import numpy as np
import sounddevice as sd

from . import providers

log = logging.getLogger("zade")


@functools.cache
def _piper(voice, data):
    from piper import PiperVoice

    return PiperVoice.load(str(pathlib.Path(data).expanduser() / "voices" / f"{voice}.onnx"))


@functools.cache
def _kokoro(data):
    from kokoro_onnx import Kokoro

    d = pathlib.Path(data).expanduser() / "kokoro"
    return Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))


def _play_async(samples, rate):
    sd.play(samples, rate)


def _playing():
    return sd.get_stream().active


def _stop():
    sd.stop()


def _wait(interrupt=None):
    """Wait for playback to end; return True if `interrupt()` fired and playback was stopped."""
    if interrupt is None:
        sd.wait()
        return False
    while _playing():
        if interrupt():
            _stop()
            return True
        time.sleep(0.05)
    return False


def _play(samples, rate, interrupt=None):
    _play_async(samples, rate)
    return _wait(interrupt)


def clean(text):
    """Drop what should never be read aloud: emoji and symbols (else "smiling face with smiling eyes"), markdown."""
    text = "".join(c for c in text if unicodedata.category(c) not in ("So", "Cs", "Cf") and c != "\ufe0f")
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_#`]", "", text)
    return " ".join(text.split())


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _speak_sentences(text, synth, interrupt, done):
    """Synthesize the next sentence while the current one plays, so long answers start right away. `done`
    counts the sentences already spoken: if synthesis fails partway, the sentence playing is let finish and
    a fallback voice carries on from there instead of starting over."""
    pending = None
    for s in sentences(text):
        try:
            audio = synth(s)
        except Exception:
            if pending:
                _wait(interrupt)
            raise
        if pending and _wait(interrupt):
            return True
        _play_async(*audio)
        pending = audio
        done.append(s)
    return bool(pending) and _wait(interrupt)


def _speak_kokoro(text, cfg, interrupt=None, done=None):
    t = cfg["tts"]
    k = _kokoro(cfg["paths"]["data"])
    lang = "en-gb" if t["voice"].startswith("b") else "en-us"
    return _speak_sentences(text, lambda s: k.create(s, voice=t["voice"], speed=t["speed"], lang=lang), interrupt,
                            [] if done is None else done)


OFFLINE_INDIAN = "hf_alpha"  # Kokoro's Indian voice, used when an online Edge voice can't be reached


def _edge_audio(sentence, voice, speed):
    """One sentence from Microsoft Edge's online neural voices (e.g. en-IN-NeerjaNeural) as 24 kHz PCM."""
    import asyncio
    import subprocess

    import edge_tts

    async def fetch():
        mp3 = b""
        async for chunk in edge_tts.Communicate(sentence, voice, rate=f"{round((speed - 1) * 100):+d}%").stream():
            if chunk["type"] == "audio":
                mp3 += chunk["data"]
        return mp3

    mp3 = asyncio.run(asyncio.wait_for(fetch(), timeout=8))
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "-", "-ac", "1", "-ar", "24000", "-f", "s16le", "-"],
                         input=mp3, capture_output=True, check=True).stdout
    if not pcm:
        raise RuntimeError("no audio from Edge TTS")
    return np.frombuffer(pcm, np.int16).astype(np.float32) / 32768, 24000


def _speak_edge(text, cfg, interrupt=None, done=None):
    t = cfg["tts"]
    return _speak_sentences(text, lambda s: _edge_audio(s, t["voice"], t["speed"]), interrupt,
                            [] if done is None else done)


def _speak_piper(text, cfg, interrupt=None):
    voice = _piper(cfg["tts"]["piper_voice"], cfg["paths"]["data"])
    chunks = list(voice.synthesize(text))
    if not chunks:  # "..." or "?!": nothing to say
        return False
    return _play(np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate, interrupt)


def speak(text, cfg, interrupt=None):
    """Speak text. Returns True if `interrupt()` cut it short (barge-in)."""
    text = clean(text or "")
    if not text:
        return False
    t = cfg["tts"]
    if t["provider"] == "openai":
        pc = cfg["providers"]["openai"]
        try:
            pcm = providers._client("openai", cfg).audio.speech.create(
                model=pc["tts_model"], voice=pc["tts_voice"], input=text, response_format="pcm").read()
            return _play(np.frombuffer(pcm, np.int16), 24000, interrupt)
        except Exception as e:  # any cloud failure (network, key, API): fall back to the local engine
            log.warning("cloud TTS failed, using piper: %s", e)
    done = []  # sentences already spoken: a fallback voice goes on from there, never from the start
    rest = lambda: " ".join(sentences(text)[len(done):])
    if t["provider"] == "kokoro" and t["voice"].endswith("Neural"):  # an online Indian voice (Edge)
        try:
            return _speak_edge(text, cfg, interrupt, done)
        except Exception as e:  # offline or the service changed: an Indian voice that runs locally
            log.warning("Edge TTS failed, using Kokoro %s: %s", OFFLINE_INDIAN, e)
            cfg = {**cfg, "tts": {**t, "voice": OFFLINE_INDIAN}}
            text, done = rest(), []
    if t["provider"] == "kokoro" and text:
        try:
            return _speak_kokoro(text, cfg, interrupt, done)
        except Exception as e:  # missing model files or a Kokoro error: still answer, with Piper
            log.warning("Kokoro TTS failed, using piper: %s", e)
            text = rest()
    return _speak_piper(text, cfg, interrupt) if text else False

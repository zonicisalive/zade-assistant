import collections
import logging
import pathlib
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger("zade")

RATE, FRAME = 16000, 1280
FRAME_S = FRAME / RATE


def rms(frame):
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


SAFETY_S = 180  # with no limit set, a recording still stops after 3 minutes, so a TV can't hold Zade forever


def limit_s(a):
    """The longest one request may be: audio.max_s, or with 0 (no limit) only the safety stop."""
    return a["max_s"] if a.get("max_s") else SAFETY_S


VOICE_FRAMES = 15


def decide(levels, thr, silence_s, max_s, start_timeout_s, end_ratio=0.25):
    # ponytail: energy endpointing; swap for Silero VAD if noise keeps recordings open.
    n = len(levels)
    loud = [i for i, level in enumerate(levels) if level > thr]
    if not loud:
        return "abort" if n >= round(start_timeout_s / FRAME_S) else "wait"
    if n >= round(max_s / FRAME_S):
        return "stop"
    # Someone talking in the background stays above the room-noise threshold and kept recordings open.
    # The user, close to the mic, is much louder: they have finished once the sound falls well below
    # their own voice level, whatever the room is doing. Their level is taken from the start (the first
    # ~1.2 s of speech, right after the wake word): over a long recording the background talk would drag it
    # down to the room level, and the recording would stay open until the safety stop.
    voice = float(np.median([levels[i] for i in loud[:VOICE_FRAMES]]))
    end_thr = max(thr, end_ratio * voice)
    last = max(i for i, level in enumerate(levels) if level > end_thr)
    return "stop" if n - 1 - last >= round(silence_s / FRAME_S) else "wait"


def open_stream(wait_s=120, retry_s=1.0):
    """The microphone stream. Right after login the audio session may not be up yet: wait for it here
    instead of crashing (a restart reloads every model)."""
    waited = 0.0
    while True:
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME)
            stream.start()
            return stream
        except sd.PortAudioError as e:
            if waited >= wait_s:
                raise
            if waited == 0:
                log.warning("microphone not available yet, waiting: %s", e)
            time.sleep(retry_s)
            waited += retry_s
            sd._terminate()  # PortAudio lists devices once: re-initialise to see the new one
            sd._initialize()


def read(stream):
    data, _ = stream.read(FRAME)
    return data[:, 0]


def wake_model(cfg):
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    w = cfg["wake"]
    if w["model"].endswith(".onnx"):
        model = str(pathlib.Path(w["model"]).expanduser())
    else:
        model = w["model"]  # built-in name such as "hey_jarvis"
        download_models([model])
    kw = {}
    if w["verifier"]:
        kw = {"custom_verifier_models": {pathlib.Path(model).stem: str(pathlib.Path(w["verifier"]).expanduser())},
              "custom_verifier_threshold": w["verifier_threshold"]}
    if w.get("vad_threshold"):
        kw["vad_threshold"] = w["vad_threshold"]  # Silero VAD: scores only count during real speech
    return Model(wakeword_models=[model], inference_framework="onnx", **kw)


def speech_threshold(noise_levels, floor, factor=2.5):
    """Loudness that counts as speech: a multiple of the room's noise, never below the configured floor."""
    if not noise_levels:
        return floor
    # 20th percentile: the quiet moments, even if you were talking for most of the window.
    return max(floor, round(float(np.percentile(noise_levels, 20)) * factor))


NOISE_FRAMES = 125  # ~10 s of room sound kept while waiting for the wake word
noise = []


RECENT_FRAMES = 25   # ~2 s of audio kept for the wake word's second opinion
COOLDOWN_FRAMES = 12  # ~1 s ignored after a rejected wake, so the same sound can't re-trigger


def wait_for_wake(stream, model, threshold, poll=None, verify=None, sure=1.01):
    """Return "wake" on the wake word, or whatever `poll()` returns when it is truthy (e.g. "hotkey").
    With `verify`, a candidate wake only counts if verify(last ~2 s of audio) agrees, unless the wake model
    is sure (score >= `sure`): the tiny Whisper check mishears accented "hey Zade" as "is it" or "hey dude"."""
    model.reset()
    recent = collections.deque(maxlen=RECENT_FRAMES)
    cooldown = 0
    while True:
        f = read(stream)
        recent.append(f)
        noise.append(rms(f))
        del noise[:-NOISE_FRAMES]
        if poll is not None and (reason := poll()):
            return reason
        score = max(model.predict(f).values())  # always fed, so the model's audio history stays continuous
        if cooldown:
            cooldown -= 1
            continue
        if score >= threshold:
            if verify is None or score >= sure or verify(np.concatenate(recent)):
                return "wake"
            log.info("wake rejected by the second check (score %.2f)", score)
            cooldown = COOLDOWN_FRAMES


def _speech_at_end(window, silence_s):
    """Whether Silero VAD hears speech in the last `silence_s` of `window` (a TV's music, a fan or keys aren't)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    ts = get_speech_timestamps(window.astype(np.float32) / 32768, VadOptions(threshold=0.3, min_speech_duration_ms=150))
    return any(t["end"] > len(window) - silence_s * RATE for t in ts)


VAD_EVERY = 4  # frames (~0.3 s) between voice checks while waiting for the end


def record(stream, cfg, start_timeout_s=None, released=None, cancelled=None, on_level=None, hands_free_if_early=False,
           speech_at_end=_speech_at_end):
    """Record one utterance. With `released` (push-to-talk), stop when it returns True, not on silence; with
    hands_free_if_early, letting go before saying anything (holding only until the chime) keeps listening and
    ends on silence instead. `cancelled` returning True drops the recording (returns None).
    Besides loudness, it ends when the voice detector hears no speech for silence_s: steady sound louder than
    the room (music, a fan, a game) kept recordings open until the safety stop."""
    a = cfg["audio"]
    thr = speech_threshold(noise, a["rms_threshold"], a["noise_factor"])
    frames, levels = [], []

    def done(why):
        log.info("recorded %.1fs, ended by %s (speech threshold %d)", len(frames) * FRAME_S, why, thr)
        return np.concatenate(frames)

    while True:
        f = read(stream)
        frames.append(f)
        levels.append(rms(f))
        if on_level is not None:  # 0 at room noise, ~0.8 for normal speech, capped at 1 (overlay waveform)
            room = thr / a["noise_factor"]
            on_level(max(0.0, min(1.0, (levels[-1] - room) / (room * 5))))
        if released is not None:
            if cancelled is not None and cancelled():
                return None
            let_go = released()
            if let_go and hands_free_if_early and max(levels) <= thr:
                released = None  # let go before speaking: listen hands-free from here
                log.info("key released before speech, listening hands-free")
            elif let_go or len(frames) >= round(limit_s(a) / FRAME_S):
                return done("the key")
            else:
                continue
        d = decide(levels, thr, a["silence_s"], limit_s(a), start_timeout_s or a["start_timeout_s"],
                   a.get("end_ratio", 0.25))
        if d == "abort":
            return None
        if d == "stop":
            return done("the time limit" if len(frames) >= round(limit_s(a) / FRAME_S) else "silence")
        first = next((i for i, level in enumerate(levels) if level > thr), len(levels))
        if len(frames) - first >= round(1.2 / FRAME_S) and len(frames) % VAD_EVERY == 0:
            window = np.concatenate(frames[-round(2.0 / FRAME_S):])
            try:
                if not speech_at_end(window, a["silence_s"]):
                    return done("no more speech")
            except Exception as e:  # the voice detector is a help, never a reason to lose the recording
                log.warning("voice check failed: %s", e)


def drain(stream):
    """Drop audio buffered while Zade was speaking, so it never hears itself (FR-4)."""
    if n := stream.read_available:
        stream.read(n)


def chime_wave(style, volume, followup=False):
    """The listening sound. soft: a gentle rising two-tone; classic: a short beep; none: silence.
    Follow-up listening uses a quieter version."""
    if style == "none":
        return None
    peak = volume * 0.4 * (0.5 if followup else 1.0)
    if style == "classic":
        t = np.linspace(0, 0.12, int(RATE * 0.12), False)
        return (peak * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    parts = []
    for freq in ((587, 880) if not followup else (660, 784)):
        t = np.linspace(0, 0.07, int(RATE * 0.07), False)
        envelope = np.sin(np.pi * t / 0.07)  # fade in and out: no click
        parts.append(peak * envelope * np.sin(2 * np.pi * freq * t))
    return np.concatenate(parts).astype(np.float32)


def cue(stream, soft=False, cfg=None):
    """Play the listening sound, then drop it from the mic buffer so it is not recorded as speech."""
    snd = (cfg or {}).get("sound", {"chime": "soft", "volume": 0.6})
    wave = chime_wave(snd["chime"], snd["volume"], followup=soft)
    if wave is not None:
        sd.play(wave, RATE)
        sd.wait()
    drain(stream)

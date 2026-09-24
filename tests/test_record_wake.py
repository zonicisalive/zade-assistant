import numpy as np

from zade import audio, record_wake


def test_trim_keeps_the_speech_with_padding():
    rng = np.random.default_rng(0)
    clip = rng.normal(0, 50, audio.RATE * 3).astype(np.int16)             # quiet room
    clip[audio.RATE:audio.RATE * 2] = rng.normal(0, 4000, audio.RATE)     # 1 s of speech
    out = record_wake.trim(clip)
    assert 1.0 * audio.RATE <= len(out) <= 1.6 * audio.RATE
    assert record_wake.trim(rng.normal(0, 50, audio.RATE * 2).astype(np.int16)) is None

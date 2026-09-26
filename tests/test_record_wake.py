import numpy as np

from zade import audio, record_wake


def test_trim_keeps_the_speech_with_padding():
    rng = np.random.default_rng(0)
    clip = rng.normal(0, 50, audio.RATE * 3).astype(np.int16)             # quiet room
    clip[audio.RATE:audio.RATE * 2] = rng.normal(0, 4000, audio.RATE)     # 1 s of speech
    out = record_wake.trim(clip)
    assert 1.0 * audio.RATE <= len(out) <= 1.6 * audio.RATE
    assert record_wake.trim(rng.normal(0, 50, audio.RATE * 2).astype(np.int16)) is None


def test_new_clips_never_overwrite_old_ones(tmp_path):
    from zade import record_wake

    for i in (0, 1, 2, 4):  # clip 3 was deleted
        (tmp_path / f"positive_{i:04d}.wav").write_bytes(b"")
    assert record_wake.next_index(tmp_path, "positive") == 5
    assert record_wake.next_index(tmp_path, "negative") == 0

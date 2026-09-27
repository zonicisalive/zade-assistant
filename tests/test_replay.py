import numpy as np

from zade import replay


class FakeRing:
    def __init__(self, samples, channels):
        self.samples, self.channels = samples, channels

    def last(self, seconds):
        a = self.samples[-seconds * replay.RATE:]
        return np.repeat(a, 2, axis=1) if self.channels == 1 else a


def test_a_clip_mixes_the_newest_seconds(tmp_path):
    r = replay.Replay.__new__(replay.Replay)
    r.seconds, r.sources, r.screen = 30, "both", None
    sound = np.full((40 * replay.RATE, 2), 1000, np.int16)   # 40 s kept... only the last 30 count
    mic = np.full((10 * replay.RATE, 1), 500, np.int16)      # the mic started later: 10 s
    r.rings = {"system": FakeRing(sound, 2), "mic": FakeRing(mic, 1)}
    folder = r.save(folder=tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == ["mic and sound.mp3", "sound.mp3"]


def test_nothing_recorded_yet_says_so(tmp_path):
    r = replay.Replay.__new__(replay.Replay)
    r.seconds, r.sources, r.screen = 30, "both", None
    r.rings = {"system": FakeRing(np.zeros((10, 2), np.int16), 2)}
    try:
        r.save(folder=tmp_path)
        assert False
    except RuntimeError as e:
        assert "nothing recorded" in str(e)

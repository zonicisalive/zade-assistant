import numpy as np

from zade import replay


class FakeRing:
    def __init__(self, samples, channels):
        self.samples, self.channels = samples, channels

    def alive(self):
        return True

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


def test_a_stopped_or_empty_recorder_is_left_out(tmp_path):
    r = replay.Replay.__new__(replay.Replay)
    r.seconds, r.sources, r.screen = 30, "both", None
    dead = FakeRing(np.full((30 * replay.RATE, 1), 500, np.int16), 1)
    dead.alive = lambda: False
    r.rings = {"system": FakeRing(np.full((30 * replay.RATE, 2), 1000, np.int16), 2), "mic": dead}
    assert sorted(p.name for p in r.save(folder=tmp_path).iterdir()) == ["sound.mp3"]
    r.rings["mic"] = FakeRing(np.zeros((0, 1), np.int16), 1)  # started, nothing yet
    assert sorted(p.name for p in r.save(folder=tmp_path / "b").iterdir()) == ["sound.mp3"]


def test_a_start_that_fails_stops_what_already_started(monkeypatch):
    started, stopped = [], []

    class Ring:
        def __init__(self, *a):
            started.append(self)

        def stop(self):
            stopped.append(self)

    def screen(*a):
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(replay, "Ring", Ring)
    monkeypatch.setattr(replay, "Screen", screen)
    monkeypatch.setattr(replay, "recorder", lambda hidden: "wf-recorder")
    try:
        replay.Replay(30, "both", screen=True)
        assert False
    except OSError:
        pass
    assert len(started) == 2 and stopped == started


def test_heal_restarts_only_the_recorders_that_stopped(monkeypatch):
    class Ring:
        def __init__(self, *a):
            self.up = True

        def alive(self):
            return self.up

        def stop(self):
            self.up = False

    monkeypatch.setattr(replay, "Ring", Ring)
    r = replay.Replay(30, "both")
    mic, system = r.rings["mic"], r.rings["system"]
    mic.up = False
    assert r.heal() == ["mic"] and r.rings["system"] is system and r.rings["mic"] is not mic
    assert r.heal() == []

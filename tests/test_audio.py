from zade import audio

A = dict(thr=500, silence_s=0.8, max_s=10.0, start_timeout_s=4.0)


def lv(*spec):
    out = []
    for level, secs in spec:
        out += [level] * round(secs / audio.FRAME_S)
    return out


def test_waits_then_aborts_when_nothing_said():
    assert audio.decide(lv((0, 1.0)), **A) == "wait"
    assert audio.decide(lv((0, 4.0)), **A) == "abort"


def test_stops_after_trailing_silence():
    assert audio.decide(lv((0, 0.5), (2000, 1.0), (0, 0.4)), **A) == "wait"
    assert audio.decide(lv((0, 0.5), (2000, 1.0), (0, 0.8)), **A) == "stop"


def test_stops_at_max_length():
    assert audio.decide(lv((2000, 10.0)), **A) == "stop"


def test_speech_after_long_pause_still_counts():
    assert audio.decide(lv((0, 3.0), (2000, 0.5)), **A) == "wait"

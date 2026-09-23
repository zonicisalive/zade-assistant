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


def test_cue_drops_chime_from_mic_buffer(monkeypatch):
    order = []
    monkeypatch.setattr(audio, "chime", lambda: order.append("chime"))
    monkeypatch.setattr(audio, "drain", lambda s: order.append("drain"))
    audio.cue(object())
    assert order == ["chime", "drain"]


def test_speech_threshold_follows_room_noise():
    assert audio.speech_threshold([950] * 25, floor=500) == 2375
    assert audio.speech_threshold([100] * 25, floor=500) == 500  # never below the configured floor
    assert audio.speech_threshold([], floor=500) == 500


def test_speech_threshold_ignores_a_few_loud_frames():
    assert audio.speech_threshold([900] * 20 + [8000] * 5, floor=500) == 2250

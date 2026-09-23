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


def test_threshold_stays_at_room_level_when_mostly_speech():
    # You talked right before "hey zade": most of the window is speech, the floor must still be the room.
    levels = [950] * 10 + [6000] * 15
    assert audio.speech_threshold(levels, floor=500) == 2375


def test_hotkey_trigger_wakes_without_wake_word(monkeypatch):
    import threading

    import numpy as np

    monkeypatch.setattr(audio, "read", lambda stream: np.zeros(audio.FRAME, np.int16))

    class NeverWakes:
        def reset(self):
            pass

        def predict(self, frame):
            return {"hey_zade": 0.0}

    trigger = threading.Event()
    trigger.set()
    audio.wait_for_wake(object(), NeverWakes(), 0.5, trigger)  # returns instead of looping forever
    assert not trigger.is_set()  # consumed, so the next wait needs a new press


def _fake_mic(monkeypatch, levels):
    import numpy as np

    frames = iter(levels)
    monkeypatch.setattr(audio, "read", lambda stream: np.full(audio.FRAME, next(frames), np.int16))
    monkeypatch.setattr(audio, "noise", [])


def test_push_to_talk_ends_on_release_not_silence(monkeypatch):
    import copy

    from zade import config

    cfg = copy.deepcopy(config.DEFAULTS)
    # speech, a 2 s pause (would end a normal recording), more speech, then release
    _fake_mic(monkeypatch, [3000] * 10 + [0] * 25 + [3000] * 10 + [0] * 50)
    reads = {"n": 0}
    real_read = audio.read

    def counting(stream):
        reads["n"] += 1
        return real_read(stream)

    monkeypatch.setattr(audio, "read", counting)
    held = lambda: reads["n"] < 45  # key released after 45 frames
    out = audio.record(object(), cfg, released=lambda: not held())
    assert len(out) == 45 * audio.FRAME


def test_push_to_talk_cancel_returns_nothing(monkeypatch):
    import copy

    from zade import config

    _fake_mic(monkeypatch, [3000] * 50)
    n = {"reads": 0}
    real_read = audio.read

    def counting(stream):
        n["reads"] += 1
        return real_read(stream)

    monkeypatch.setattr(audio, "read", counting)
    out = audio.record(object(), copy.deepcopy(config.DEFAULTS),
                       released=lambda: False, cancelled=lambda: n["reads"] >= 10)
    assert out is None

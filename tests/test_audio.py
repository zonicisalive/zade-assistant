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


def test_cue_plays_then_drops_chime_from_mic_buffer(monkeypatch):
    order = []
    monkeypatch.setattr(audio.sd, "play", lambda wave, rate: order.append("play"))
    monkeypatch.setattr(audio.sd, "wait", lambda: order.append("wait"))
    monkeypatch.setattr(audio, "drain", lambda s: order.append("drain"))
    audio.cue(object())
    assert order == ["play", "wait", "drain"]
    order.clear()
    audio.cue(object(), cfg={"sound": {"chime": "none", "volume": 0.6}})
    assert order == ["drain"]  # silent, but still clears the mic buffer


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

    def poll():
        if trigger.is_set():
            trigger.clear()
            return "hotkey"

    assert audio.wait_for_wake(object(), NeverWakes(), 0.5, poll) == "hotkey"  # instead of looping forever
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


def test_voice_level_is_zero_at_room_noise_and_full_when_speaking(monkeypatch):
    import copy

    import numpy as np

    from zade import config

    cfg = copy.deepcopy(config.DEFAULTS)
    monkeypatch.setattr(audio, "noise", [1000] * 25)  # room noise ~1000 -> speech threshold 2500
    frames = iter([1000, 2500, 5000, 20000])
    monkeypatch.setattr(audio, "read", lambda stream: np.full(audio.FRAME, next(frames), np.int16))
    seen = []
    n = {"i": 0}

    def released():
        n["i"] += 1
        return n["i"] >= 4

    audio.record(object(), cfg, released=released, on_level=seen.append)
    assert seen[0] < 0.05          # room noise: flat
    assert 0.15 < seen[1] < 0.5    # quiet speech
    assert seen[2] > 0.6           # normal speech
    assert seen[3] == 1.0          # loud: capped


def test_wake_needs_the_second_opinion(monkeypatch):
    import numpy as np

    monkeypatch.setattr(audio, "read", lambda stream: np.full(audio.FRAME, 100, np.int16))
    monkeypatch.setattr(audio, "noise", [])
    scores = iter([0.1, 0.9, 0.1] + [0.1] * 12 + [0.9] + [0.1] * 50)  # two candidate wakes

    class Model:
        def reset(self):
            pass

        def predict(self, frame):
            return {"hey_zade": next(scores)}

    verdicts = iter([False, True])  # the first candidate was noise, the second was real
    heard = []

    def verify(clip):
        heard.append(len(clip))
        return next(verdicts)

    assert audio.wait_for_wake(object(), Model(), 0.5, verify=verify) == "wake"
    assert len(heard) == 2 and heard[0] > 0  # the recent audio was handed to the checker


def test_background_talk_does_not_keep_the_recording_open():
    # you at 6000, then your mother across the room at 900 (above the 500 room threshold)
    assert audio.decide(lv((0, 0.3), (6000, 1.5), (900, 0.5)), **A) == "wait"
    assert audio.decide(lv((0, 0.3), (6000, 1.5), (900, 0.9)), **A) == "stop"
    # a quiet speaker isn't cut off: 900 is their own voice level, so it still counts as talking
    assert audio.decide(lv((0, 0.3), (900, 1.5), (900, 0.9)), **A) == "wait"

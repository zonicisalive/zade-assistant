import numpy as np

from zade import calibrate


def test_recommend_for_a_noisy_room():
    rng = np.random.default_rng(1)
    noise = np.concatenate([rng.normal(1100, 250, 60), rng.uniform(2000, 4500, 10)])  # spiky background
    voice = rng.normal(3300, 1500, 200).clip(700)
    r = calibrate.recommend(noise, voice)
    assert r["noise_factor"] >= 3.0 and r["noise_pct"] <= 10 and r["voice_pct"] >= 30  # was 14% noise at 2.5


def test_quiet_room_keeps_a_low_setting():
    noise = np.full(70, 200.0)
    voice = np.full(200, 4000.0)
    r = calibrate.recommend(noise, voice)
    assert r["noise_factor"] == 2.0 and r["threshold"] == 500 and r["noise_pct"] == 0


def test_room_louder_than_voice_is_flagged():
    noise = np.random.default_rng(2).uniform(1000, 5000, 70)
    voice = np.full(200, 2500.0)
    assert calibrate.recommend(noise, voice)["too_noisy"]


def test_voice_step_needs_the_quiet_step(tmp_path):
    assert calibrate.run("voice", tmp_path)["ok"] is False


def test_gate_keeps_speech_and_mutes_the_room():
    from zade import audio
    rng = np.random.default_rng(3)
    room = rng.normal(0, 300, audio.RATE).astype(np.int16)
    voice = rng.normal(0, 5000, audio.RATE).astype(np.int16)
    out = calibrate.gate(np.concatenate([room, voice]), thr=1500)
    assert not out[:audio.RATE - audio.FRAME * 3].any()          # room muted
    assert np.array_equal(out[audio.RATE + audio.FRAME:], np.concatenate([room, voice])[audio.RATE + audio.FRAME:])


def test_stats_for_another_setting(tmp_path, monkeypatch):
    from zade import audio
    rng = np.random.default_rng(4)
    np.save(tmp_path / "calibration_room.npy", rng.normal(0, 800, 5 * audio.RATE).astype(np.int16))
    np.save(tmp_path / "calibration_voice.npy", rng.normal(0, 6000, 6 * audio.RATE).astype(np.int16))
    monkeypatch.setattr(calibrate, "speech_levels", lambda clip: calibrate.levels(clip))  # no VAD model needed
    low, high = calibrate.stats(tmp_path, 2.0), calibrate.stats(tmp_path, 5.0)
    assert low["threshold"] < high["threshold"] and low["noise_factor"] == 2.0

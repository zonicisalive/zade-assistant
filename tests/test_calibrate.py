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

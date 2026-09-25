import copy

import numpy as np

from zade import config, voice_focus

RATE = 16000


def test_focus_cuts_another_voice_and_keeps_yours(tmp_path):
    c = copy.deepcopy(config.DEFAULTS)
    c["paths"]["data"] = str(tmp_path)  # no saved owner voiceprint
    audio = np.arange(5 * RATE, dtype=np.int16)
    spans = [(0, RATE), (RATE + 3200, 2 * RATE), (3 * RATE, 4 * RATE)]  # you, someone else, you
    you, other = np.array([1.0, 0.0]), np.array([0.0, 1.0])

    def embed(clip, data_dir):
        return other if clip[0] == RATE + 3200 else you

    out = voice_focus.focus(audio, c, embed=embed, pieces=lambda a: spans)
    gap = np.zeros(int(0.2 * RATE), np.int16)
    assert np.array_equal(out, np.concatenate([audio[:RATE], gap, audio[3 * RATE:4 * RATE]]))  # other voice gone
    assert voice_focus.focus(audio, c, embed=lambda *a: you, pieces=lambda a: spans) is audio  # nothing cut
    assert voice_focus.focus(audio, c, embed=embed, pieces=lambda a: spans[:1]) is audio      # one piece


def test_owner_voiceprint_is_used_when_the_owner_speaks(tmp_path):
    c = copy.deepcopy(config.DEFAULTS)
    c["paths"]["data"] = str(tmp_path)
    you, other = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    assert voice_focus.enroll([np.zeros(10, np.int16)] * 3, tmp_path, embed=lambda clip, d: you) == 3
    audio = np.arange(4 * RATE, dtype=np.int16) % 1000
    spans = [(0, RATE), (2 * RATE + 500, 3 * RATE + 500)]  # the second piece starts with sample value 500
    # your first piece is a bit off (a cold, a whisper); the owner profile still recognises you, and the
    # other voice is cut against the profile
    wobbly = np.array([0.8, 0.6])
    embed = lambda clip, d: wobbly if clip is not None and len(clip) and clip[0] == 0 else other
    out = voice_focus.focus(audio, c, embed=embed, pieces=lambda a: spans)
    assert len(out) == RATE

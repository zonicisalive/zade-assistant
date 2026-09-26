from zade import guide

LABELS = [{"text": "Settings", "x": 100, "y": 200}, {"text": "Integrations", "x": 100, "y": 240}]


def test_locate_exact_fuzzy_and_copied_coordinates():
    assert guide.locate("Integrations", LABELS)["y"] == 240
    assert guide.locate("integration", LABELS)["y"] == 240           # close spelling
    assert guide.locate("100,240: Integrations", LABELS)["y"] == 240  # the model copied the position
    assert guide.locate("Bluetooth", LABELS) is None and guide.locate(None, LABELS) is None


def test_plan_reads_the_models_json(monkeypatch):
    import ollama

    class R:
        class message:
            content = '{"done": false, "label": "Integrations", "say": "Click Integrations"}'

    monkeypatch.setattr(ollama.Client, "chat", lambda self, **k: R)
    cfg = {"llm": {"host": "http://x", "model": "m", "keep_alive": "30s"}}
    assert guide.plan("spotify keys", LABELS, [], cfg) == {"done": False, "label": "Integrations", "say": "Click Integrations"}
    R.message.content = "not json"
    assert guide.plan("x", LABELS, [], cfg)["label"] is None

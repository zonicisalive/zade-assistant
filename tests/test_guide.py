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
    cfg = {"llm": {"host": "http://x", "model": "m", "keep_alive": "30s"}, "guide": {"provider": "local", "model": ""}}
    assert guide.plan("spotify keys", LABELS, [], cfg) == {"done": False, "label": "Integrations", "point": None,
                                                           "say": "Click Integrations"}
    R.message.content = "not json"
    assert guide.plan("x", LABELS, [], cfg)["label"] is None


def test_cloud_model_points_at_icons_and_falls_back(monkeypatch):
    import numpy as np

    img = np.zeros((1440, 2560, 3), np.uint8)
    cfg = {"llm": {"host": "http://x", "model": "m", "keep_alive": "30s"},
           "guide": {"provider": "openai", "model": "gemini-2.5-flash"}, "providers": {"openai": {"model": "x"}}}
    monkeypatch.setattr(guide, "_cloud", lambda system, msg, img, cfg: (
        '```json\n{"done": false, "label": null, "x": 640, "y": 100, "say": "Click the gear icon"}\n```', 2.0))
    p = guide.plan("open settings", LABELS, [], cfg, img=img)
    assert p["point"] == (1280, 200) and p["label"] is None       # 1280-px screenshot -> screen pixels

    def down(*a):
        raise RuntimeError("no key")

    monkeypatch.setattr(guide, "_cloud", down)
    monkeypatch.setattr(guide, "_local", lambda system, msg, cfg: '{"done": true, "say": "Done"}')
    assert guide.plan("open settings", LABELS, [], cfg, img=img)["done"] is True  # fell back to local

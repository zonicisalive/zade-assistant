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
           "guide": {"provider": "openai", "model": "z-ai/glm-4.6v", "pointer": "cloud"},
           "providers": {"openai": {"model": "x"}}}
    asked = []

    def cloud(system, msg, img, cfg):
        asked.append(msg)
        if system == guide.POINT:  # the second request: where the icon is
            return '```json\n{"point_2d": [500, 100]}\n```'
        return '{"done": false, "label": null, "icon": "the gear at the top", "say": "Click the gear icon"}'

    monkeypatch.setattr(guide, "_cloud", cloud)
    p = guide.plan("open settings", LABELS, [], cfg, img=img)
    assert p["point"] == (1280, 144) and p["label"] is None       # 0-1000 of the screenshot -> screen pixels
    assert asked[-1] == "Point at the gear at the top"

    def down(*a):
        raise RuntimeError("no key")

    monkeypatch.setattr(guide, "_cloud", down)
    monkeypatch.setattr(guide, "_local", lambda system, msg, cfg: '{"done": true, "say": "Done"}')
    assert guide.plan("open settings", LABELS, [], cfg, img=img)["done"] is True  # fell back to local


def test_point_reads_every_reply_shape():
    import numpy as np

    img = np.zeros((1440, 2560, 3), np.uint8)
    assert guide._point('{"point_2d": [19, 87]}', img) == (49, 125)
    assert guide._point('{"x": "19", "y": 87}', img) == (49, 125)
    assert guide._point('{"x": [19, 87]}', img) == (49, 125)
    assert guide._point('{"x": 2360, "y": 14}', img) is None   # off the 0-1000 scale
    assert guide._point("I can't see it", img) is None


def test_icons_are_found_by_the_local_pointer_first(monkeypatch):
    import numpy as np

    img = np.zeros((1440, 2560, 3), np.uint8)
    cfg = {"guide": {"provider": "openai", "model": "m", "pointer": "local"}}
    monkeypatch.setattr(guide, "_point_local", lambda what, img, cfg: (48, 126))
    monkeypatch.setattr(guide, "_cloud", lambda *a: (_ for _ in ()).throw(AssertionError("cloud not needed")))
    assert guide.find("the Discord home icon", img, cfg) == (48, 126)

    def down(*a):
        raise RuntimeError("not running")

    monkeypatch.setattr(guide, "_point_local", down)
    monkeypatch.setattr(guide, "_cloud", lambda system, msg, img, cfg: '{"point_2d": [19, 87]}')
    assert guide.find("the Discord home icon", img, cfg) == (49, 125)  # fell back to the cloud model


def test_local_agent_actions_become_spoken_steps(monkeypatch):
    import numpy as np

    img = np.zeros((1440, 2560, 3), np.uint8)
    cfg = {"guide": {"provider": "local", "pointer": "local"}}
    labels = [{"text": "Integrations", "x": 256, "y": 144}]
    replies = iter(["<action>Click(box=(100, 100))</action>", "<action>RightClick(box=(900, 900))</action>",
                    "<action>Type(content='hello\\n')</action>", "<action>Hotkey(keys=['ctrl', 'k'])</action>",
                    "<action>Swipe(amount=-5, axis='vertical')</action>", "<action>Finished(content='')</action>"])
    monkeypatch.setattr(guide, "_pointer_ask", lambda *a, **k: next(replies))
    step = lambda: guide.plan("x", labels, [], cfg, img=img)
    assert step() == {"done": False, "label": None, "point": (256, 144), "say": "Click Integrations"}
    assert step()["say"] == "Right-click here"            # nothing readable near that spot
    assert step()["say"] == "Type hello and press Enter"
    assert step()["say"] == "Press ctrl plus k"
    assert step()["say"] == "Scroll down"
    assert step() == {"done": True, "label": None, "point": None, "say": "That's done."}


def test_zades_own_bubble_is_not_a_thing_to_click():
    own = ["click the message bar of discord", "Point out message bar of Discord.",
           "Click the message bar at the bottom of the Discord window."]
    assert guide.is_own("Click the message bar at the bottom of the Discord window.", own)
    assert guide.is_own("Point out message bar of Discord.", own)
    assert not guide.is_own("Message @DEXORTO", own) and not guide.is_own("Direct Messages", own)
    assert not guide.is_own("User Settings", ["open my user settings"])

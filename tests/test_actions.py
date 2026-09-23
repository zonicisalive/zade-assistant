import pytest

from zade import actions


def test_is_yes():
    for t in ["yes", "Yeah, do it.", "sure", "OK", "go ahead"]:
        assert actions.is_yes(t), t
    for t in ["", "no", "yes no", "yesterday", "don't", "wait what"]:
        assert not actions.is_yes(t), t


def test_root_commands_refused_without_asking():
    asked = []
    for cmd in ["sudo rm -rf /", "echo x | sudo tee /etc/f", "pkexec bash", "SUDO ls", "doas reboot"]:
        with pytest.raises(actions.Failed):
            actions.shell(cmd, lambda q: asked.append(q) or True)
    assert asked == []


def test_shell_needs_yes(tmp_path):
    f = tmp_path / "x"
    with pytest.raises(actions.Failed):
        actions.shell(f"touch {f}", lambda q: False)
    assert not f.exists()
    assert actions.shell("echo hi", lambda q: True) == "hi"


def test_shell_timeout(monkeypatch):
    monkeypatch.setattr(actions, "TIMEOUT_S", 0.2)
    with pytest.raises(actions.Failed, match="too long"):
        actions.shell("sleep 2", lambda q: True)


def test_find_app(tmp_path):
    (tmp_path / "org.mozilla.firefox.desktop").write_text(
        "[Desktop Entry]\nName=Firefox\nExec=/usr/lib/firefox/firefox %u\n")
    (tmp_path / "hidden.desktop").write_text("[Desktop Entry]\nName=Hidden\nExec=hidden\nNoDisplay=true\n")
    (tmp_path / "broken.desktop").write_text("not an ini file")
    assert actions.find_app("firefox", [tmp_path]) == ("org.mozilla.firefox", "firefox")
    assert actions.find_app("hidden", [tmp_path]) is None
    assert actions.find_app("photoshop", [tmp_path]) is None


def test_volume_command(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "_call", calls.append)
    actions.run({"name": "volume", "args": {"delta": -10}}, lambda q: True)
    actions.run({"name": "volume", "args": {"set": 150}}, lambda q: True)
    assert calls == [
        ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "10%-"],
        ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "100%"],
    ]


def test_open_missing_app(monkeypatch):
    monkeypatch.setattr(actions, "find_app", lambda name: None)
    with pytest.raises(actions.Failed, match="couldn't find"):
        actions.run({"name": "open_app", "args": {"name": "kitty"}}, lambda q: True)


def test_unknown_action():
    with pytest.raises(actions.Failed):
        actions.run({"name": "format_disk", "args": {}}, lambda q: True)


def test_is_yes_rejects_yes_plus_other_words():
    for t in ["do it later", "run it tomorrow", "yeah hold on", "yes but not that one",
              "ok so what does that do", "ok google", "it"]:
        assert not actions.is_yes(t), t
    for t in ["yes please", "okay run it", "yeah go ahead"]:
        assert actions.is_yes(t), t


def test_root_check_sees_through_quoting():
    for cmd in ["s\\udo ls", "su\"\"do ls", "run0 ls", "'sudo' ls"]:
        with pytest.raises(actions.Failed):
            actions.shell(cmd, lambda q: True)


def test_browser_means_the_default_browser(tmp_path, monkeypatch):
    (tmp_path / "avahi-discover.desktop").write_text("[Desktop Entry]\nName=Avahi Zeroconf Browser\nExec=avahi-discover\n")
    (tmp_path / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\nExec=/usr/lib/firefox/firefox %u\n")
    monkeypatch.setattr(actions, "_default_browser", lambda: "firefox.desktop")
    for name in ["browser", "the browser", "web browser", "internet"]:
        assert actions.find_app(name, [tmp_path]) == ("firefox", "firefox"), name


def _calls(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "_call", calls.append)
    monkeypatch.setattr(actions, "_spawn", calls.append)
    return calls


def test_window_actions(monkeypatch):
    calls = _calls(monkeypatch)
    actions.run({"name": "window", "args": {"action": "close"}}, lambda q: True)
    actions.run({"name": "window", "args": {"action": "workspace", "workspace": "bot"}}, lambda q: True)
    actions.run({"name": "window", "args": {"action": "move_to_workspace", "workspace": 2}}, lambda q: True)
    assert calls == [
        ["niri", "msg", "action", "close-window"],
        ["niri", "msg", "action", "focus-workspace", "bot"],
        ["niri", "msg", "action", "move-window-to-workspace", "2"],
    ]
    with pytest.raises(actions.Failed):
        actions.run({"name": "window", "args": {"action": "explode"}}, lambda q: True)


def test_open_website(monkeypatch):
    calls = _calls(monkeypatch)
    for site in ["YouTube", "github.com", "some band"]:
        actions.run({"name": "open_website", "args": {"site": site}}, lambda q: True)
    assert calls == [
        ["xdg-open", "https://www.youtube.com"],
        ["xdg-open", "https://github.com"],
        ["xdg-open", "https://duckduckgo.com/?q=some+band"],
    ]


def test_clipboard_and_typing(monkeypatch):
    fed = []
    monkeypatch.setattr(actions, "_feed", lambda cmd, text: fed.append((cmd, text)))
    monkeypatch.setattr(actions, "_output", lambda cmd: "copied text\n")
    assert actions.run({"name": "clipboard_read", "args": {}}, lambda q: True) == "Your clipboard says: copied text"
    actions.run({"name": "clipboard_copy", "args": {"text": "hello"}}, lambda q: True)
    calls = _calls(monkeypatch)
    actions.run({"name": "type_text", "args": {"text": "hello world"}}, lambda q: True)
    assert fed == [(["wl-copy"], "hello")]
    assert calls == [["wtype", "--", "hello world"]]


def test_brightness_and_screenshot(monkeypatch):
    calls = _calls(monkeypatch)
    actions.run({"name": "brightness", "args": {"set": 150}}, lambda q: True)
    actions.run({"name": "brightness", "args": {"delta": -20}}, lambda q: True)
    actions.run({"name": "screenshot", "args": {}}, lambda q: True)
    assert calls == [
        ["ddcutil", "setvcp", "10", "100"],
        ["ddcutil", "setvcp", "10", "-", "20"],
        ["niri", "msg", "action", "screenshot-screen"],
    ]


def test_power_needs_yes(monkeypatch):
    calls = _calls(monkeypatch)
    asked = []
    with pytest.raises(actions.Failed, match="Cancelled"):
        actions.run({"name": "power", "args": {"action": "shutdown"}}, lambda q: asked.append(q) or False)
    assert calls == [] and asked == ["Shut down the computer?"]
    actions.run({"name": "power", "args": {"action": "suspend"}}, lambda q: True)
    assert calls == [["systemctl", "suspend"]]


def test_open_app_falls_back_to_website(monkeypatch):
    calls = _calls(monkeypatch)
    monkeypatch.setattr(actions, "find_app", lambda name: None)
    actions.run({"name": "open_app", "args": {"name": "Netflix"}}, lambda q: True)
    assert calls == [["xdg-open", "https://www.netflix.com"]]

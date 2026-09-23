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

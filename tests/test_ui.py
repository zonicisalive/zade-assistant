import json

from zade import ui


def test_show_writes_state_and_keeps_text(tmp_path, monkeypatch):
    f = tmp_path / "state.json"
    monkeypatch.setattr(ui, "PATH", f)
    ui.reset()
    ui.show("listening")
    ui.show("thinking", heard="open firefox")
    ui.show("speaking", reply="Opening Firefox.")
    s = json.loads(f.read_text())
    assert (s["state"], s["heard"], s["reply"]) == ("speaking", "open firefox", "Opening Firefox.")
    ui.show("listening", heard="", reply="")
    assert json.loads(f.read_text())["reply"] == ""


def test_level_is_clamped(tmp_path, monkeypatch):
    f = tmp_path / "state.json"
    monkeypatch.setattr(ui, "PATH", f)
    ui.reset()
    ui.level(3.0)
    assert json.loads(f.read_text())["level"] == 1.0


def test_write_errors_never_break_zade(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "PATH", tmp_path / "missing-dir" / "sub" / "x" / "state.json")
    (tmp_path / "missing-dir").write_text("a file, so mkdir fails")
    ui.show("listening")  # must not raise


def test_no_second_overlay_when_the_desktop_shell_hosts_it(tmp_path, monkeypatch):
    import copy
    import subprocess

    from zade import config

    shell = tmp_path / "shell.qml"
    shell.write_text('ShellRoot {\n    LazyLoader { active: true; source: "file:///home/me/Zade/ui/Overlay.qml" }\n}\n')
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["ui"]["host_file"] = str(shell)
    monkeypatch.setattr(ui, "PATH", tmp_path / "state.json")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    assert ui.start(cfg) is None and started == []
    shell.write_text("ShellRoot {}\n")  # an inir update removed the hook: fall back to our own overlay
    ui.start(cfg)
    assert len(started) == 1


def test_host_file_hook_is_recognised(tmp_path, monkeypatch):
    import copy
    import subprocess

    from zade import config

    shell = tmp_path / "shell.qml"
    shell.write_text('LazyLoader { active: true; source: "file:///home/me/Zade/ui/ZadeHost.qml" }\n')
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["ui"]["host_file"] = str(shell)
    monkeypatch.setattr(ui, "PATH", tmp_path / "state.json")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    assert ui.start(cfg) is None and started == []

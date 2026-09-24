import json

import pytest

from zade import config, ctl, memory


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('# my settings\n[llm]\nmodel = "qwen3:4b-instruct"\n')
    monkeypatch.setattr(ctl, "CONFIG", cfg_file)
    monkeypatch.setattr(ctl, "DB", tmp_path / "zade.db")
    monkeypatch.setattr(ctl, "INBOX", tmp_path / "inbox")
    return tmp_path


def run(*args):
    return json.loads(ctl.main(list(args)))


def test_set_setting_keeps_other_user_settings(env):
    assert run("set", "tts.speed", "1.3") == {"ok": True}
    assert run("set", "ui.enabled", "false") == {"ok": True}
    assert run("set", "llm.personality", "Be sarcastic.") == {"ok": True}
    cfg = config.load(env / "config.toml")
    assert cfg["tts"]["speed"] == 1.3 and cfg["ui"]["enabled"] is False
    assert cfg["llm"]["model"] == "qwen3:4b-instruct" and cfg["llm"]["personality"] == "Be sarcastic."
    assert run("settings")["tts"]["speed"] == 1.3


def test_unknown_setting_is_refused(env):
    assert run("set", "tts.colour", "red")["ok"] is False


def test_memory_editing(env):
    run("fact-add", "my editor is nvim")
    (f,) = run("facts")
    assert f["text"] == "my editor is nvim"
    assert run("fact-del", str(f["id"])) == {"ok": True}
    assert run("facts") == []

    conn = memory.connect(env / "zade.db")
    memory.add_shortcut(conn, "dev", [{"name": "open_app", "args": {"name": "code"}}])
    assert run("shortcut-rename", "dev", "work time") == {"ok": True}
    assert [s["phrase"] for s in run("shortcuts")] == ["work time"]
    assert run("shortcut-del", "work time") == {"ok": True}

    run("reminder-add", "drink water", "9 am", "daily")
    (r,) = run("reminders")
    assert r["message"] == "drink water" and r["daily"] is True
    assert run("reminder-del", str(r["id"])) == {"ok": True}


def test_history_and_typed_commands(env):
    conn = memory.connect(env / "zade.db")
    memory.log_request(conn, "open firefox", "Done.", "pattern", 50)
    (h,) = run("history")
    assert (h["heard"], h["reply"], h["route"], h["ms"]) == ("open firefox", "Done.", "pattern", 50)
    assert run("say", "what time is it") == {"ok": True}
    assert (env / "inbox").read_text() == "what time is it\n"


def test_status_uses_the_lock_not_a_stale_pid(env, monkeypatch):
    from zade import __main__ as z

    monkeypatch.setattr(ctl, "LOCK", env / "zade.lock")
    assert run("status")["running"] is False
    held = z.single_instance(env / "zade.lock")
    assert run("status")["running"] is True
    assert run("start") == {"ok": True, "already": True}  # never starts a second copy
    held.close()

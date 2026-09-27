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
    assert run("set", "tts.speed", "1.3") == {"ok": True, "live": False}  # needs a restart
    assert run("set", "ui.enabled", "false") == {"ok": True, "live": False}
    assert run("set", "llm.personality", "Be sarcastic.") == {"ok": True, "live": True}
    assert run("set", "music.mode", "connect")["live"] is True
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


def test_status_reports_ready_only_for_the_running_zade(env, monkeypatch):
    from zade import __main__ as z

    monkeypatch.setattr(ctl, "LOCK", env / "zade.lock")
    monkeypatch.setattr(ctl, "READY", env / "zade.ready")
    held = z.single_instance(env / "zade.lock")
    monkeypatch.setattr(ctl, "_pid", lambda: 4242)
    assert run("status")["ready"] is False          # starting up
    (env / "zade.ready").write_text("4242")
    assert run("status")["ready"] is True
    (env / "zade.ready").write_text("1111")         # left over from an older Zade
    assert run("status")["ready"] is False
    held.close()


def test_api_keys_are_saved_privately_and_never_read_back(env, monkeypatch):
    monkeypatch.setattr(ctl, "ENV", env / "env")
    (env / "env").write_text("# keys\nOTHER=keep\n")
    assert run("key-set", "SPOTIFY_CLIENT_ID", "abc123") == {"ok": True}
    assert run("key-set", "SPOTIFY_CLIENT_ID", "new456") == {"ok": True}
    assert run("key-set", "EVIL", "x")["ok"] is False
    keys = run("keys")
    assert keys["SPOTIFY_CLIENT_ID"] is True and keys["ANTHROPIC_API_KEY"] is False
    assert "new456" not in json.dumps(keys)
    text = (env / "env").read_text()
    assert "OTHER=keep" in text and "SPOTIFY_CLIENT_ID=new456" in text and "abc123" not in text
    assert (env / "env").stat().st_mode & 0o777 == 0o600
    assert run("key-set", "SPOTIFY_CLIENT_ID", "") == {"ok": True}
    assert run("keys")["SPOTIFY_CLIENT_ID"] is False


def test_the_app_sends_a_key_on_stdin_not_the_command_line(env, monkeypatch):
    import io

    monkeypatch.setattr(ctl, "ENV", env / "env")
    monkeypatch.setattr(ctl.sys, "stdin", io.StringIO("sk-secret\n"))
    assert run("key-set", "OPENAI_API_KEY") == {"ok": True}
    assert "OPENAI_API_KEY=sk-secret" in (env / "env").read_text()


def test_shortcut_phrases_are_stored_the_way_speech_is_matched(env):
    step = json.dumps([{"name": "open_app", "args": {"name": "steam"}}])
    assert run("shortcut-save", "Hey Zade, Gaming Mode!", step) == {"ok": True}
    assert [s["phrase"] for s in run("shortcuts")] == ["gaming mode"]


def test_your_own_wake_word_model(env, monkeypatch):
    import pathlib

    import openwakeword

    monkeypatch.setattr(ctl, "DATA", env)
    model = pathlib.Path(openwakeword.__file__).parent / "resources" / "models" / "alexa_v0.1.onnx"
    if not model.exists():
        pytest.skip("openWakeWord's models aren't downloaded")
    junk = env / "notes.onnx"
    junk.write_text("not a model")
    assert not run("wake-add", str(junk), "hey", "notes")["ok"]
    assert not run("wake-add", str(model))["ok"]                        # no phrase
    r = run("wake-add", str(model), "Hey", "Computer!")
    assert r["ok"] and r["phrase"] == "Hey Computer" and (env / "wake" / "hey_computer.onnx").exists()
    assert config.load(env / "config.toml")["wake"]["model"].endswith("wake/hey_computer.onnx")
    words = run("wake-list")["words"]
    assert ["hey_jarvis", "Hey Jarvis", False] in words and [r["model"], "Hey Computer", True] in words
    assert not run("wake-del", "hey_jarvis")["ok"]                      # built-in: not removable
    assert run("wake-del", str(env / "wake" / "hey_computer.onnx"))["ok"]
    assert config.load(env / "config.toml")["wake"]["model"] == "hey_jarvis"  # was in use: back to a built-in


def test_quiet_hours_must_be_times_of_day(env):
    assert not run("set", "quiet.start", "11pm")["ok"] and not run("set", "quiet.end", "25:00")["ok"]
    assert run("set", "quiet.start", "23:00")["ok"]


def test_settings_saved_at_the_same_moment_are_all_kept(env):
    from concurrent.futures import ThreadPoolExecutor

    keys = [("stt.provider", "whisper"), ("stt.model", "base.en"), ("sound.volume", "0.5"), ("ui.position", "bottom")]
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda kv: ctl.set_setting(*kv), keys * 5))
    assert all(r["ok"] for r in results)
    c = config.load(env / "config.toml")
    assert (c["stt"]["provider"], c["stt"]["model"], c["sound"]["volume"], c["ui"]["position"]) == \
        ("whisper", "base.en", 0.5, "bottom")


def test_control_characters_in_a_setting_keep_the_file_readable(env):
    assert run("set", "llm.personality", "be nice\x0bplease\tand\x7f calm")["ok"]
    assert config.load(env / "config.toml")["llm"]["personality"] == "be nice\x0bplease\tand\x7f calm"


def test_a_key_with_a_line_break_is_refused(env, monkeypatch):
    monkeypatch.setattr(ctl, "ENV", env / "env")
    assert not ctl.set_key("OPENAI_API_KEY", "sk-abc\nANTHROPIC_API_KEY=evil")["ok"]
    assert ctl.set_key("OPENAI_API_KEY", "  sk-abc\n")["ok"]  # a trailing newline from pasting is fine
    assert (env / "env").read_text() == "OPENAI_API_KEY=sk-abc\n"

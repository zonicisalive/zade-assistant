import json
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
    monkeypatch.setattr(actions, "_output", lambda cmd: "Volume: 0.50\n")
    monkeypatch.setattr(actions, "RAMP_DELAY_S", 0)
    sink = ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@"]
    actions.run({"name": "volume", "args": {"delta": -10}}, lambda q: True)
    assert calls == [sink + ["40%"]]  # decreases go straight there
    calls.clear()
    actions.run({"name": "volume", "args": {"set": 70}}, lambda q: True)
    # increases ramp in 5% steps: desktop "volume protection" rejects big jumps as "Illegal increment"
    assert calls == [sink + ["55%"], sink + ["60%"], sink + ["65%"], sink + ["70%"]]
    calls.clear()
    actions.run({"name": "volume", "args": {"set": 150}}, lambda q: True)
    assert calls[-1] == sink + ["100%"] and len(calls) == 10


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
        ["xdg-open", "https://duckduckgo.com/?q=%5Csome+band"],
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


def test_user_app_names_for_hotwords(tmp_path):
    (tmp_path / "game.desktop").write_text(
        "[Desktop Entry]\nName=MECCHA CHAMELEON\nExec=steam steam://rungameid/4704690\n")
    (tmp_path / "tg.desktop").write_text("[Desktop Entry]\nName=Telegram\nExec=telegram-desktop\n")
    (tmp_path / "hidden.desktop").write_text("[Desktop Entry]\nName=Hidden\nExec=x\nNoDisplay=true\n")
    assert sorted(actions.app_names([tmp_path])) == ["MECCHA CHAMELEON", "Telegram"]


def test_closing_a_steam_game_never_kills_steam(monkeypatch):
    calls = _calls(monkeypatch)
    monkeypatch.setattr(actions, "find_app", lambda name: ("MECCHA CHAMELEON", "steam"))
    with pytest.raises(actions.Failed, match="Steam"):
        actions.run({"name": "close_app", "args": {"name": "meccha chameleon"}}, lambda q: True)
    assert calls == []


def test_unknown_app_suggests_the_closest_instead_of_guessing(tmp_path, monkeypatch):
    (tmp_path / "game.desktop").write_text("[Desktop Entry]\nName=MECCHA CHAMELEON\nExec=steam steam://x\n")
    (tmp_path / "ff.desktop").write_text("[Desktop Entry]\nName=Firefox\nExec=firefox\n")
    (tmp_path / "htop.desktop").write_text("[Desktop Entry]\nName=Htop\nExec=htop\n")
    monkeypatch.setattr(actions, "APP_DIRS", [tmp_path])
    with pytest.raises(actions.Failed, match="Did you mean MECCHA CHAMELEON"):
        actions.run({"name": "open_app", "args": {"name": "megacamillion"}}, lambda q: True)
    with pytest.raises(actions.Failed) as e:
        actions.run({"name": "open_app", "args": {"name": "photoshop"}}, lambda q: True)
    assert "Did you mean" not in str(e.value)  # nothing close: no wild suggestion


def test_long_commands_are_shown_not_read_aloud():
    asked = []
    actions.shell("echo hi", lambda q: asked.append(q) or True)
    long_cmd = "ls /usr/bin | grep -E 'firefox|thunderbird|libreoffice|gedit|emacs'"
    actions.shell(long_cmd, lambda q: asked.append(q) or True)
    assert asked == ["Run echo hi?", "Should I run this command?\n" + long_cmd]


def test_words_inside_app_ids_do_not_match(tmp_path):
    (tmp_path / "com.github.wwmm.easyeffects.desktop").write_text(
        "[Desktop Entry]\nName=Easy Effects\nExec=easyeffects\n")
    (tmp_path / "org.mozilla.firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\nExec=firefox\n")
    assert actions.find_app("github", [tmp_path]) is None           # "github" is only in the ID
    assert actions.find_app("easyeffects", [tmp_path]) == ("com.github.wwmm.easyeffects", "easyeffects")
    assert actions.find_app("easy effects", [tmp_path]) == ("com.github.wwmm.easyeffects", "easyeffects")
    assert actions.find_app("firefox", [tmp_path]) == ("org.mozilla.firefox", "firefox")


def test_never_pkill_generic_launchers(monkeypatch):
    calls = _calls(monkeypatch)
    for exe in ["sh", "bash", "env", "flatpak", "python3", "gtk-launch"]:
        monkeypatch.setattr(actions, "find_app", lambda name, e=exe: ("some-app", e))
        with pytest.raises(actions.Failed, match="close this window"):
            actions.run({"name": "close_app", "args": {"name": "spotify"}}, lambda q: True)
    assert calls == []


def test_a_long_phrase_does_not_match_an_app_inside_it(tmp_path):
    (tmp_path / "discord.desktop").write_text("[Desktop Entry]\nName=Discord\nExec=discord\n")
    assert actions.find_app("discord whatsapp and telegram", [tmp_path]) is None
    assert actions.find_app("discord", [tmp_path]) == ("discord", "discord")
    assert actions.find_app("discrd", [tmp_path]) == ("discord", "discord")  # small mishearings still match


def test_system_components_can_never_be_closed(monkeypatch):
    calls = _calls(monkeypatch)
    for name, exe in [("niri", "niri"), ("inir settings", "inir"), ("quickshell", "qs"), ("pipewire", "pipewire"),
                      ("portal", "xdg-desktop-portal"), ("zade", "zade")]:
        monkeypatch.setattr(actions, "find_app", lambda n, e=exe: ("x", e))
        with pytest.raises(actions.Failed, match="keeps your desktop running"):
            actions.run({"name": "close_app", "args": {"name": name}}, lambda q: True)
    assert calls == []


def test_mute_sets_state_instead_of_toggling(monkeypatch):
    calls = _calls(monkeypatch)
    actions.run({"name": "mute", "args": {"on": True}}, lambda q: True)
    actions.run({"name": "mute", "args": {"on": False}}, lambda q: True)
    actions.run({"name": "mute", "args": {}}, lambda q: True)  # old-style call: mute
    assert calls == [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
                     ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
                     ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]]


def test_key_combos_become_wtype_arguments():
    K = actions.wtype_args
    assert K("ctrl+c") == ["-M", "ctrl", "-k", "c", "-m", "ctrl"]
    assert K("super+2") == ["-M", "logo", "-k", "2", "-m", "logo"]
    assert K("ctrl+shift+t") == ["-M", "ctrl", "-M", "shift", "-k", "t", "-m", "shift", "-m", "ctrl"]
    assert K("enter") == ["-k", "Return"]
    assert K("alt+f4") == ["-M", "alt", "-k", "F4", "-m", "alt"]
    assert K("page down") == ["-k", "Next"]
    assert K("volume up") == ["-k", "XF86AudioRaiseVolume"]
    with pytest.raises(actions.Failed):
        K("ctrl+banana")


def test_dangerous_combos_are_blocked(monkeypatch):
    calls = _calls(monkeypatch)
    for combo in ["super+shift+e", "ctrl+alt+delete", "ctrl+alt+backspace", "win+shift+e"]:
        with pytest.raises(actions.Failed, match="won't press"):
            actions.run({"name": "press_keys", "args": {"keys": combo}}, lambda q: True)
    assert calls == []


def test_press_a_sequence(monkeypatch):
    calls = _calls(monkeypatch)
    actions.run({"name": "press_keys", "args": {"keys": "ctrl+a, ctrl+c"}}, lambda q: True)
    assert calls == [["wtype", "-M", "ctrl", "-k", "a", "-m", "ctrl"], ["wtype", "-M", "ctrl", "-k", "c", "-m", "ctrl"]]


def test_empty_combo_does_not_hide_a_closing_key():
    assert actions.is_closing("enter, , alt+f4") and actions.is_closing("ctrl+a then ctrl+w")
    assert not actions.is_closing("enter, ctrl+c")


def test_app_list_is_cached_until_an_app_is_installed(tmp_path):
    (tmp_path / "a.desktop").write_text("[Desktop Entry]\nName=Alpha\nExec=alpha\n")
    assert "alpha" in actions._apps([tmp_path])
    before = tmp_path.stat().st_mtime_ns
    (tmp_path / "b.desktop").write_text("[Desktop Entry]\nName=Beta\nExec=beta\n")
    import os
    os.utime(tmp_path, ns=(before + 10**9, before + 10**9))  # folder changed
    assert "beta" in actions._apps([tmp_path])


def test_open_app_focuses_a_running_window(monkeypatch):
    calls = _calls(monkeypatch)
    monkeypatch.setattr(actions, "find_app", lambda name: ("discord", "discord"))
    windows = [{"id": 7, "app_id": "kitty"}, {"id": 9, "app_id": "discord"}]
    monkeypatch.setattr(actions, "_output", lambda cmd: json.dumps(windows))
    actions.run({"name": "open_app", "args": {"name": "discord"}}, lambda q: True)
    assert calls == [["niri", "msg", "action", "focus-window", "--id", "9"]]
    calls.clear()
    windows.pop()  # Discord not open: launch it
    actions.run({"name": "open_app", "args": {"name": "discord"}}, lambda q: True)
    assert calls == [["gtk-launch", "discord"]]


def test_mute_takes_an_explicit_action(monkeypatch):
    calls = _calls(monkeypatch)
    assert actions.run({"name": "mute", "args": {"action": "mute"}}, lambda q: True) == "Muted."
    assert actions.run({"name": "mute", "args": {"action": "unmute"}}, lambda q: True) == "Sound is back on."
    assert actions.run({"name": "mute", "args": {"on": False}}, lambda q: True) == "Sound is back on."  # patterns
    assert [c[-1] for c in calls] == ["1", "0", "0"]


def test_send_message_uses_discords_quick_switcher(monkeypatch):
    typed = []
    monkeypatch.setattr(actions, "_call", lambda cmd: typed.append(cmd[1:]))
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    monkeypatch.setattr(actions, "run", lambda a, c: "")
    monkeypatch.setattr(actions, "_focused_app", lambda: "discord")
    assert actions.send_message("dexorto", "this is a test") == "Sent to dexorto on Discord."
    assert typed == [actions.wtype_args("ctrl+k"), ["--", "@dexorto"], actions.wtype_args("enter"),
                     ["--", "this is a test"], actions.wtype_args("enter")]


def test_commands_needing_root_or_hiding_it_are_refused():
    for cmd in ["sudo id", "/usr/bin/sud? id", "pkexe[c] id", "s$()udo id", "`echo sudo` id", "systemd-run --uid=0 id",
                "ls; /bin/s? -c id", "X=1 /usr/bin/su* id", "machinectl shell root@"]:
        assert actions.needs_root(cmd), cmd
    for cmd in ["ls *.txt", "df -h", "grep -r foo .", "echo hi | wc -c", "find . -name '*.py'"]:
        assert not actions.needs_root(cmd), cmd


def test_send_message_never_types_outside_discord(monkeypatch):
    typed = []
    monkeypatch.setattr(actions, "_call", lambda cmd: typed.append(cmd))
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    monkeypatch.setattr(actions.time, "monotonic", iter(range(0, 10000, 5)).__next__)
    monkeypatch.setattr(actions, "run", lambda a, c: "")
    monkeypatch.setattr(actions, "_focused_app", lambda: "kitty")   # a terminal kept focus
    with pytest.raises(actions.Failed):
        actions.send_message("dexorto", "rm -rf ~")
    assert typed == []

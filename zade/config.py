import copy
import os
import pathlib
import tomllib

DEFAULTS = {
    "wake": {"model": "hey_jarvis", "threshold": 0.5, "verifier": "", "verifier_threshold": 0.3,
             # 3-stage wake check: the wake model fires, Silero VAD confirms speech, then a tiny Whisper
             # model re-listens to the last 2 s and must hear "hey zade" (or a close variant).
             "vad_threshold": 0.5, "verify": True, "verify_model": "tiny.en"},
    "audio": {"rms_threshold": 500, "noise_factor": 2.5, "silence_s": 0.8, "max_s": 10.0, "start_timeout_s": 4.0},
    "stt": {
        "provider": "whisper", "model": "base.en", "device": "cpu", "beam_size": 5,
        # Words to expect: big accuracy gain for accents and made-up names (shortcut phrases are added too).
        "hotwords": ["Zade", "workspace", "timer", "remind me", "minutes", "volume", "weather", "screenshot",
                     "Firefox", "Discord", "Steam", "YouTube", "clipboard", "brightness"],
    },
    "router": {"shortcut_min_score": 90, "laya_accept": 0.90, "laya_confirm": 0.60, "laya_enabled": False},
    "llm": {
        "provider": "ollama",
        "model": "qwen3:4b-instruct",
        "host": "http://127.0.0.1:11434",
        "num_ctx": 4096,
        "keep_alive": "60s",
        "vram_min_free_gb": 4.0,
        "fallback": "cpu",
        "personality": "",  # your own instructions for how Zade talks, added to its system prompt
    },
    "providers": {
        "anthropic": {
            "model": "claude-opus-5",
            "api_key_env": "ANTHROPIC_API_KEY",
            "effort": "low",
            "fallbacks": "default",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-5-mini",
            "api_key_env": "OPENAI_API_KEY",
            "stt_model": "gpt-4o-mini-transcribe",
            "tts_model": "gpt-4o-mini-tts",
            "tts_voice": "alloy",
        },
    },
    "tts": {"provider": "kokoro", "voice": "af_heart", "speed": 1.2, "piper_voice": "en_US-lessac-medium"},
    "hotkey": {"enabled": True, "key": "KEY_LEFTMETA", "hold_s": 2.0},
    "dictation": {"enabled": True, "key": "KEY_RIGHTALT", "hold_s": 0.3},  # hold to type what you say
    "web": {"searxng_url": "http://127.0.0.1:8080"},
    "followup": {"enabled": True, "listen_s": 5.0, "history_turns": 3, "history_s": 120},
    "ui": {
        "enabled": True,
        "host_file": "~/.config/quickshell/inir/shell.qml",  # desktop shell that may host the overlay
        # Overlay style (applies live): top | bottom | top-left | top-right | bottom-left | bottom-right
        "position": "top", "size": "medium", "accent": "", "linger_s": 0.5, "reveal_cps": 18, "show_heard": True,
    },
    "sound": {"chime": "soft", "volume": 0.6, "wake_reply": ""},  # chime: soft | classic | none
    # The character in the overlay. type: blob | robot | cat | bunny | bear | ghost;
    # shape: round | squircle | blob; eyes: round | oval | anime | line; mouth: smile | cat | small;
    # color: "" = wallpaper accent.
    "persona": {"enabled": True, "name": "Zade", "type": "blob", "shape": "round", "eyes": "round",
                "mouth": "smile", "color": "", "blush": True},
    "quiet": {"enabled": False, "start": "23:00", "end": "08:00", "dnd": False},
    "safety": {"confirm": "commands"},  # commands | risky | everything
    "vision": {"model": "qwen2.5vl:3b"},
    "learning": {"promote_after": 3},
    "paths": {"data": "~/.local/share/zade"},
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    p = pathlib.Path(path or "~/.config/zade/config.toml").expanduser()
    user = tomllib.loads(p.read_text()) if p.exists() else {}
    return _merge(copy.deepcopy(DEFAULTS), user)


def load_env(path=None):
    """Read KEY=VALUE lines (API keys) from ~/.config/zade/env; variables already set win."""
    p = pathlib.Path(path or "~/.config/zade/env").expanduser()
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def dumps(data, prefix=""):
    """TOML for Zade's nested settings (tables of strings, numbers, booleans and lists)."""
    lines = [f"{k} = {_toml_value(v)}" for k, v in data.items() if not isinstance(v, dict)]
    for k, v in data.items():
        if isinstance(v, dict):
            name = f"{prefix}{k}"
            body = dumps(v, name + ".")
            if any(not isinstance(x, dict) for x in v.values()):
                lines.append(f"\n[{name}]")
            lines.append(body)
    return "\n".join(line for line in lines if line)

import copy
import pathlib
import tomllib

DEFAULTS = {
    "wake": {"model": "hey_jarvis", "threshold": 0.5, "verifier": "", "verifier_threshold": 0.3},
    "audio": {"rms_threshold": 500, "silence_s": 0.8, "max_s": 10.0, "start_timeout_s": 4.0},
    "stt": {"provider": "whisper", "model": "small.en", "device": "cpu"},
    "router": {"shortcut_min_score": 90, "laya_accept": 0.90, "laya_confirm": 0.60, "laya_enabled": False},
    "llm": {
        "provider": "ollama",
        "model": "qwen3:4b-instruct",
        "host": "http://127.0.0.1:11434",
        "num_ctx": 4096,
        "keep_alive": "60s",
        "vram_min_free_gb": 4.0,
        "fallback": "cpu",
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
    "tts": {"provider": "piper", "voice": "en_US-lessac-medium"},
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

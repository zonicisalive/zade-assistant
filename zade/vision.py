"""Look at the screen: screenshot + local vision model (qwen2.5vl via Ollama).

The text model is unloaded first and the vision model unloads right after answering,
so Zade stays within its VRAM budget.
"""

import os
import pathlib
import subprocess

import httpx
import ollama

from . import brain
from .actions import Failed

SHOT = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "screen.png"


def _screenshot(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["grim", "-s", "0.5", str(path)], check=True, capture_output=True, timeout=10)  # half size: faster


def _client(cfg):
    return ollama.Client(host=cfg["llm"]["host"], timeout=120)


def look(question, cfg):
    try:
        _screenshot(SHOT)
        brain.unload(cfg)
        r = _client(cfg).chat(
            model=cfg["vision"]["model"],
            messages=[{"role": "user", "images": [str(SHOT)], "content":
                       f"{question}\nThis is a screenshot of the user's screen. Answer in at most three short "
                       "sentences of plain spoken text. Quote error messages exactly when asked about errors."}],
            keep_alive=0,
            options={"num_ctx": 4096},
        )
        return (r.message.content or "").strip() or "I couldn't make out anything on the screen."
    except (OSError, subprocess.SubprocessError, ollama.ResponseError, httpx.HTTPError) as e:
        raise Failed("I couldn't look at the screen right now.") from e
    finally:
        SHOT.unlink(missing_ok=True)  # never leave screenshots lying around

"""Look at the screen: screenshot + local vision model (via Ollama).

With a separate vision model (qwen2.5vl) the text model is unloaded first and the vision model unloads right
after answering, so Zade stays within its VRAM budget. With vision.model "" the brain itself looks (it must
see images, like qwen3.5): nothing is unloaded or reloaded.
"""

import os
import pathlib
import subprocess

import httpx
import ollama

from . import brain
from .actions import Failed

SHOT = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "screen.png"


def _screenshot(path, scale):
    from .replay import screen_output

    path.parent.mkdir(parents=True, exist_ok=True)
    output = screen_output()  # the focused monitor only
    subprocess.run(["grim", "-s", str(scale), *(["-o", output] if output else []), str(path)], check=True,
                   capture_output=True, timeout=10)


def _client(cfg):
    return ollama.Client(host=cfg["llm"]["host"], timeout=120)


def look(question, cfg):
    own = not cfg["vision"].get("model")  # the brain looks itself
    try:
        # 3/4 size for the brain (small text like an ID is unreadable at half size; full size took 13 s
        # instead of 6 on a 1440p screen), half for a small separate model (it reads little text anyway)
        _screenshot(SHOT, 0.75 if own else 0.5)
        if not own:
            brain.unload(cfg)
        r = _client(cfg).chat(
            model=cfg["llm"]["model"] if own else cfg["vision"]["model"],
            messages=[{"role": "system", "content": "You read screenshots accurately and answer only what is asked."},
                      {"role": "user", "images": [str(SHOT)], "content":
                       f"{question}\nThis is a screenshot of the user's screen. Answer in at most three short "
                       "sentences of plain spoken text. When asked for text on the screen (an ID, a number, a code, "
                       "an error), quote it exactly, character for character; if you can't read it clearly, say so "
                       "and never guess."}],
            think=False if own else None,
            keep_alive=cfg["llm"]["keep_alive"] if own else 0,
            options={"num_ctx": 8192 if own else 4096},  # a 3/4-size 1440p screenshot is ~3,000 tokens
        )
        return (r.message.content or "").strip() or "I couldn't make out anything on the screen."
    except (OSError, subprocess.SubprocessError, ollama.ResponseError, httpx.HTTPError) as e:
        raise Failed("I couldn't look at the screen right now.") from e
    finally:
        SHOT.unlink(missing_ok=True)  # never leave screenshots lying around

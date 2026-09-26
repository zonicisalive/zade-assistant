"""Screen guide: point at what to click for a task, step by step, and click only when asked.

"Guide me to turn on screen share in Discord" -> Zade reads the screen (OCR gives every piece of text and
its exact position), the language model picks the next thing to click from that list and says one short
instruction, a glowing ring points at it (ui/pointer.qml), and when the screen changes (the user clicked)
it looks again for the next step. "Click it" clicks the pointed spot through a small virtual absolute
pointer (exact pixels, unaffected by mouse acceleration). OCR finds text; an icon without text can't be
pointed at yet.
"""

import functools
import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
import threading
import time

import numpy as np

log = logging.getLogger("zade")

STATE = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "pointer.json"
POINTER_QML = pathlib.Path(__file__).resolve().parent.parent / "ui" / "pointer.qml"
PLAN = ("You guide a user through a task on their Linux desktop, one click at a time. You get the goal, the "
        "steps already done, and every piece of text visible on screen as 'x,y: text'. First look for visible text "
        "that directly matches the goal or its key words (goal 'turn on do not disturb' -> 'Do not disturb'; 'API "
        "keys for Spotify' -> 'Integrations' or 'Spotify'; 'what it remembers' -> 'Memory'); click that. Only if "
        "nothing matches, pick the menu or button most likely to lead there. Reply with JSON only: "
        '{"done": true|false, "label": "the text to click, copied exactly without its x,y", '
        '"say": "one short spoken instruction, e.g. Click Integrations in the menu on the left"}. Set done true '
        "when the goal is reached: when the last step clicked the thing the goal names, or the screen now shows it "
        "(its page, panel or setting). If what's needed isn't on screen, set label null and say what to open first.")


# ── Seeing the screen ─────────────────────────────────────────────────────────────────────────────────
def screenshot(scale=1.0):
    """The screen as an RGB array (grim)."""
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        subprocess.run(["grim", "-s", str(scale), f.name], check=True, capture_output=True, timeout=10)
        return np.array(Image.open(f.name).convert("RGB"))


@functools.cache
def _ocr():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read_screen(img):
    """Every piece of text on screen: [{"text", "x", "y"}] with x, y the centre in screen pixels."""
    result, _ = _ocr()(img)
    return [{"text": text.strip(), "x": round(sum(p[0] for p in box) / 4), "y": round(sum(p[1] for p in box) / 4)}
            for box, text, conf in (result or []) if text.strip() and conf > 0.5]


def locate(label, labels):
    """The on-screen position of the text the model chose (exact match first, then the closest spelling)."""
    from rapidfuzz import fuzz, process

    if not label:
        return None
    label = re.sub(r"^\s*\d+\s*,\s*\d+\s*:\s*", "", label)  # the model sometimes copies "x,y: " too
    exact = [l for l in labels if l["text"].lower() == label.lower()]
    if exact:
        return exact[0]
    hit = process.extractOne(label.lower(), [l["text"].lower() for l in labels], scorer=fuzz.ratio, score_cutoff=80)
    return labels[hit[2]] if hit else None


_GUIDE_POINT = (" If you can see the screenshot and the thing to click has no text (an icon), set label null and "
                "give \"x\" and \"y\" in screenshot pixels.")


def _parse(text):
    """The first JSON object in a model's reply (cloud models like to wrap it in ```json fences)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m[0]) if m else {}
    except ValueError:
        return {}


def _cloud(system, msg, img, cfg):
    """Ask a cloud model that can see: Claude or any OpenAI-compatible one (Gemini, OpenRouter, ...). The
    screenshot goes at 1280 px wide; returns (reply text, factor from its pixels to the screen's)."""
    import base64
    import io

    from PIL import Image

    from . import providers

    provider = cfg["guide"]["provider"]
    model = cfg["guide"]["model"] or cfg["providers"][provider]["model"]
    factor = img.shape[1] / 1280
    buf = io.BytesIO()
    Image.fromarray(img).resize((1280, round(img.shape[0] / factor))).save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    client = providers._client(provider, cfg).with_options(timeout=40)
    if provider == "anthropic":
        r = client.messages.create(model=model, max_tokens=500, system=system, messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": msg}]}])
        return r.content[0].text, factor
    r = client.chat.completions.create(model=model, temperature=0, extra_body=providers._private(client), messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": [{"type": "text", "text": msg},
                                     {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}])
    return r.choices[0].message.content, factor


def _local(system, msg, cfg):
    import ollama

    r = ollama.Client(host=cfg["llm"]["host"], timeout=60).chat(
        model=cfg["llm"]["model"], format="json", keep_alive="2m",  # stays loaded between steps
        options={"temperature": 0, "num_predict": 250, "num_ctx": 8192},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": msg}])
    return r.message.content


def plan(goal, labels, done_steps, cfg, img=None):
    """The next step: {"done", "label", "point", "say"}. point is an (x, y) on screen when the model pointed at
    something without text (only cloud models see the screenshot)."""
    listing = "\n".join(f"{l['x']},{l['y']}: {l['text']}" for l in labels[:220])
    msg = f"Goal: {goal}\nDone so far: {'; '.join(done_steps) or 'nothing'}\nOn screen:\n{listing}"
    system = PLAN + _GUIDE_POINT
    out, factor = {}, 1.0
    if cfg.get("guide", {}).get("provider", "local") != "local" and img is not None:
        try:
            text, factor = _cloud(system, msg, img, cfg)
            out = _parse(text)
        except Exception as e:  # no key, offline, quota: the local model still works
            log.warning("cloud screen model failed, using the local one: %s", e)
    if not out:
        out, factor = _parse(_local(system, msg, cfg)), 1.0
    point = None
    if out.get("x") is not None and out.get("y") is not None:
        try:
            point = (round(float(out["x"]) * factor), round(float(out["y"]) * factor))
        except (TypeError, ValueError):
            point = None
    return {"done": bool(out.get("done")), "label": out.get("label") or None, "point": point,
            "say": str(out.get("say") or "I'm not sure what to click next.")}


# ── Showing and clicking ──────────────────────────────────────────────────────────────────────────────
def show_pointer(x=None, y=None, text=""):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"visible": x is not None, "x": x or 0, "y": y or 0, "text": text}))
    os.replace(tmp, STATE)


class Clicker:
    """A virtual absolute pointer (like a VM's tablet mouse): moves to exact pixels, then clicks."""

    def __init__(self, width, height):
        from evdev import AbsInfo, UInput, ecodes as e

        self.e = e
        self.ui = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
                          e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, width - 1, 0, 0, 0)),
                                     (e.ABS_Y, AbsInfo(0, 0, height - 1, 0, 0, 0))]},
                         name="zade-pointer", version=0x3)
        time.sleep(0.8)  # the compositor needs a moment to pick the new device up

    def click(self, x, y):
        e = self.e
        self.ui.write(e.EV_ABS, e.ABS_X, int(x))
        self.ui.write(e.EV_ABS, e.ABS_Y, int(y))
        self.ui.syn()
        time.sleep(0.12)
        for value in (1, 0):
            self.ui.write(e.EV_KEY, e.BTN_LEFT, value)
            self.ui.syn()
            time.sleep(0.05)

    def close(self):
        self.ui.close()


# ── A guiding session ─────────────────────────────────────────────────────────────────────────────────
class Session:
    """Guides toward one goal until it's done, the user says stop, or nothing happens for a while."""

    def __init__(self, goal, cfg, say, idle_s=90):
        self.goal, self.cfg, self.say, self.idle_s = goal, cfg, say, idle_s
        self.done_steps, self.target = [], None
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self._clicker = None
        show_pointer(None)  # the overlay starts hidden
        self._overlay = subprocess.Popen(["qs", "-p", str(POINTER_QML)], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def step(self):
        """Look at the screen and point at the next thing. Returns what was said."""
        img = screenshot()
        labels = read_screen(img)
        p = plan(self.goal, labels, self.done_steps, self.cfg, img=img)
        if p["done"]:
            self.stop()
            return p["say"] if p["say"] else "That's done."
        spot = locate(p["label"], labels)
        # the exact text position (OCR) if there is one, else where a cloud model pointed (an icon)
        self.target = (spot["x"], spot["y"], p["label"]) if spot else \
            ((*p["point"], "the spot") if p["point"] else None)
        show_pointer(*(self.target[:2] if self.target else (None, None)), p["say"])
        if self.target:
            self.done_steps.append(f"pointed at {self.target[2]}")
        return p["say"]

    def _run(self):
        try:
            self.say(self.step())
            last_change = time.monotonic()
            base = screenshot(0.25)
            while not self.stopped.is_set():
                self.wake.wait(1.0)
                if self.stopped.is_set():
                    break
                now = screenshot(0.25)
                changed = self.wake.is_set() or float(np.abs(now.astype(np.int16) - base).mean()) > 3.0
                self.wake.clear()
                if changed:
                    time.sleep(0.8)  # let the new screen finish drawing
                    self.say(self.step())
                    base, last_change = screenshot(0.25), time.monotonic()
                elif time.monotonic() - last_change > self.idle_s:
                    self.say("I'll stop guiding now. Ask again any time.")
                    break
        except Exception as e:  # never take the assistant down with it
            log.warning("guide failed: %s", e)
            self.say("Sorry, I lost track of the screen.")
        finally:
            self.stop()

    def click(self):
        if not self.target:
            return "There's nothing to click yet."
        if self._clicker is None:
            h, w = screenshot(0.25).shape[:2]
            self._clicker = Clicker(w * 4, h * 4)
        x, y, label = self.target
        show_pointer(None)
        self._clicker.click(x, y)
        self.done_steps[-1:] = [f"clicked {label}"]
        self.wake.set()  # look again right away
        return ""

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        show_pointer(None)
        if self._clicker:
            self._clicker.close()
        if self._overlay.poll() is None:
            self._overlay.terminate()

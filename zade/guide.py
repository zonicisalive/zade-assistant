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
import math
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


_GUIDE_POINT = (" If you can see the screenshot and the thing to click has no readable text of its own (an icon, "
                "the X that closes a tab or window, an arrow), don't give the text beside it: set label null and add "
                '"icon": a short description of it and where it is on screen.')
# Pointing gets its own request: with the long text list in the same request, models point far off.
POINT = ('Point at one thing on the screenshot. Reply JSON only: {"point_2d": [x, y]}, its centre from 0 to 1000 '
         'across and down (0,0 top left, 1000,1000 bottom right).')


def _parse(text):
    """The first JSON object in a model's reply (cloud models like to wrap it in ```json fences)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m[0]) if m else {}
    except ValueError:
        return {}


def _point(text, img):
    """Screen pixels from a pointing reply. Models answer as {"point_2d": [x, y]}, {"x": .., "y": ..} or a
    bare pair, from 0 to 1000 whatever pixel size the image had; anything off that scale is a confused model."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", re.sub(r"\w*_2d", "", text or ""))[:2]
    if len(nums) < 2:
        return None
    x, y = map(float, nums)
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    return round(x * img.shape[1] / 1000), round(y * img.shape[0] / 1000)


POINTER_SERVICE = "zade-gui"  # UI-Venus-2-9B: a model trained to find things on screenshots and act on them
VENUS = ("Output the center point of the position corresponding to the instruction: {}. The output should just be "
         "the coordinates of a point, in the format [x,y].")
# UI-Venus's own computer-agent prompt (github.com/inclusionAI/UI-Venus, models/computer), cut to what a guide
# can show the user. It then plans the next step itself, without anything leaving the computer.
AGENT = """**You are a GUI Agent.**
Your role is to analyze the user's task and guide them through it one action at a time on a desktop operating system.

### Available Actions
You may execute one of the following functions. Coordinates range from the top-left corner (0, 0) to the bottom-right corner (999, 999).
- Click(box=(x1, y1))
- DoubleClick(box=(x1, y1))
- RightClick(box=(x1, y1))
- Hover(box=(x1, y1))
> Move the cursor to the coordinate WITHOUT clicking (to reveal a submenu).
- Swipe(amount=-5, axis='vertical')
> Scroll: vertical positive scrolls up and negative scrolls down.
- Type(content='')
> Type the provided text into the focused field. Each `\n` presses Enter.
- Hotkey(keys=['ctrl', 'c'])
- Wait()
- CallUser(content='')
> Report failure when the task cannot be completed or additional information is required.
- Finished(content='')
> Mark the task as completed successfully.

### Instructions
- Make sure you understand the task goal to avoid wrong actions.
- One atomic action per turn.
- Make sure you carefully examine the current screenshot. The steps done so far might not be reliable.
- Use `Finished` only after successful completion.

### Output Format
<action> the next action </action>

### User Task
{task}"""


def _pointer_ask(text, img, cfg, system=None, max_tokens=30, prefill=None, wait_s=30):
    """Ask the local pointer model about the screenshot (sent at 1920 px wide). Waits while it is still loading."""
    import base64
    import io
    import urllib.error
    import urllib.request

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(img).resize((1920, round(img.shape[0] * 1920 / img.shape[1]))).save(buf, "PNG")
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}}
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": [{"type": "text", "text": text}, image] if system else [image, {"type": "text", "text": text}]}] + (
        [{"role": "assistant", "content": prefill}] if prefill else [])  # answer starts here: no rambling first
    body = json.dumps({"temperature": 0, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
                       "messages": messages}).encode()
    url = cfg["guide"].get("pointer_url", "http://127.0.0.1:8191").rstrip("/") + "/v1/chat/completions"
    deadline = time.monotonic() + wait_s
    while True:
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except (urllib.error.URLError, ConnectionError) as e:  # still loading (refused, or 503 while it loads)
            if time.monotonic() > deadline:
                raise RuntimeError(f"the screen pointer isn't answering: {e}") from e
            time.sleep(1)


def _point_local(what, img, cfg):
    """Where `what` is on screen, from the local pointer model. It was tested pointing within 5 px at icons
    that cloud models missed by hundreds."""
    return _point(_pointer_ask(VENUS.format(what), img, cfg), img)


def _agent_step(goal, labels, done_steps, img, cfg):
    """The next step from the local agent model, as plan() returns it. What it says is built from its action
    and the text nearest the spot ("Click Integrations")."""
    task = goal + (f"\nSteps done so far: {'; '.join(done_steps)}" if done_steps else "")
    reply = _pointer_ask("Current Screenshot:\n", img, cfg, system=AGENT.format(task=task), max_tokens=80,
                         prefill="<action>")
    action = reply.split("</action>")[0].replace("<action>", "").strip()
    name = (re.match(r"(\w+)", action) or [""])[0]
    content = (re.search(r"content=(['\"])(.*?)\1\s*\)", action, re.S) or [None, None, ""])[2].strip()
    if name in ("Finished", "CallUser"):
        return {"done": True, "label": None, "point": None, "say": content or "That's done."}
    point = _point(box[1], img) if (box := re.search(r"box=\(([^)]*)\)", action)) else None
    if point:
        near = min(labels, key=lambda l: math.dist((l["x"], l["y"]), point), default=None)
        readable = near and re.search(r"[A-Za-z0-9]{2}", near["text"])  # not OCR noise like "口"
        what = near["text"] if readable and math.dist((near["x"], near["y"]), point) < 60 else "here"
        verb = {"DoubleClick": "Double-click", "RightClick": "Right-click", "Hover": "Point at"}.get(name, "Click")
        return {"done": False, "label": None, "point": point, "say": f"{verb} {what}"}
    if name == "Type":
        enter = content.endswith("\\n") or content.endswith("\n")
        text = content.replace("\\n", "").replace("\n", "")
        return {"done": False, "label": None, "point": None, "say": f"Type {text}" + (" and press Enter" if enter else "")}
    if name == "Hotkey":
        keys = re.findall(r"['\"]([^'\"]+)['\"]", action)
        return {"done": False, "label": None, "point": None, "say": "Press " + " plus ".join(keys)}
    if name == "Swipe":
        down = re.search(r"amount=\s*-", action)
        return {"done": False, "label": None, "point": None, "say": "Scroll down" if down else "Scroll up"}
    return {"done": False, "label": None, "point": None, "say": "Wait a moment."}


def _cloud(system, msg, img, cfg):
    """Ask a cloud model that can see: Claude or any OpenAI-compatible one (NanoGPT, OpenRouter, ...), with the
    screenshot at 1280 px wide."""
    import base64
    import io

    from PIL import Image

    from . import providers

    provider = cfg["guide"]["provider"]
    model = cfg["guide"]["model"] or cfg["providers"][provider]["model"]
    buf = io.BytesIO()
    Image.fromarray(img).resize((1280, round(img.shape[0] * 1280 / img.shape[1]))).save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    client = providers._client(provider, cfg).with_options(timeout=40)
    if provider == "anthropic":
        r = client.messages.create(model=model, max_tokens=500, system=system, messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": msg}]}])
        return r.content[0].text
    r = client.chat.completions.create(model=model, temperature=0, extra_body=providers._private(client), messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                                     {"type": "text", "text": msg}]}])
    return r.choices[0].message.content


def _local(system, msg, cfg):
    import ollama

    r = ollama.Client(host=cfg["llm"]["host"], timeout=60).chat(
        model=cfg["llm"]["model"], format="json", keep_alive="2m",  # stays loaded between steps
        options={"temperature": 0, "num_predict": 250, "num_ctx": 8192},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": msg}])
    return r.message.content


def find(what, img, cfg):
    """Screen pixels of something without text (an icon), or None."""
    if cfg["guide"].get("pointer", "local") == "local":
        try:
            return _point_local(what, img, cfg)
        except Exception as e:  # not installed or not answering: the planning model can still try
            log.warning("local screen pointer failed, asking the cloud model: %s", e)
    return _point(_cloud(POINT, f"Point at {what}", img, cfg), img)


def plan(goal, labels, done_steps, cfg, img=None):
    """The next step: {"done", "label", "point", "say"}. point is an (x, y) on screen when a cloud model (only
    they see the screenshot) chose something without text; a second request finds where it is."""
    listing = "\n".join(f"{l['x']},{l['y']}: {l['text']}" for l in labels[:220])
    msg = f"Goal: {goal}\nDone so far: {'; '.join(done_steps) or 'nothing'}\nOn screen:\n{listing}"
    out, point = {}, None
    g = cfg.get("guide", {})
    if g.get("provider", "local") == "local" and g.get("pointer", "local") == "local" and img is not None:
        try:  # the local agent model sees the screen: nothing leaves the computer
            return _agent_step(goal, labels, done_steps, img, cfg)
        except Exception as e:
            log.warning("local screen agent failed, using the text-only model: %s", e)
    if g.get("provider", "local") != "local" and img is not None:
        try:
            out = _parse(_cloud(PLAN + _GUIDE_POINT, msg, img, cfg))
            icon = out.get("icon") or out.get("label")  # models also put the icon's description in label
            if icon and not locate(out.get("label"), labels):
                point = find(icon, img, cfg)
        except Exception as e:  # no key, offline, quota: the local model still works
            log.warning("cloud screen model failed, using the local one: %s", e)
    if not out:
        out = _parse(_local(PLAN, msg, cfg))
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
        self._pointer = cfg["guide"].get("pointer", "local") == "local"
        if self._pointer:  # loads while the first step is planned; stopped with the guide, so VRAM is freed
            subprocess.run(["systemctl", "--user", "start", "--no-block", POINTER_SERVICE], capture_output=True)
        show_pointer(None)  # the overlay starts hidden
        self._overlay = subprocess.Popen(["qs", "-p", str(POINTER_QML)], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def step(self):
        """Look at the screen and point at the next thing. Returns what was said."""
        show_pointer(None)  # else the ring and its instruction are in the screenshot and get read back
        time.sleep(0.15)
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
        if self._pointer:
            subprocess.run(["systemctl", "--user", "stop", "--no-block", POINTER_SERVICE], capture_output=True)

import atexit
import datetime
import fcntl
import logging
import os
import pathlib
import re
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import actions, brain, config, info, memory, router

log = logging.getLogger("zade")
# Tools whose calls are never learned as shortcuts (memory, one-off content, or risky).
STOP_WORDS = {"stop", "cancel", "never mind", "nevermind", "shut up", "quiet", "be quiet", "nothing"}
MEMORY_TOOLS = {"express", "dnd", "look_at_screen", "system_status", "set_reminder", "list_reminders", "cancel_reminder", "sync_apps", "remember", "forget", "list_facts", "make_shortcut", "sleep", "set_timer", "note_add",
                "notes_read", "web_answer", "clipboard_read", "clipboard_copy", "type_text", "power", "shell"}


def _duration(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    parts = [f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in ((h, "hour"), (m, "minute"), (s, "second")) if n]
    return " ".join(parts) or "0 seconds"


@dataclass
class Ctx:
    cfg: dict
    conn: sqlite3.Connection
    say: Callable[[str], None]
    confirm: Callable[[str], bool]
    predict: Callable | None = None
    find_app: Callable = actions.find_app
    ask: Callable = brain.ask
    run_action: Callable = actions.run
    alerts: list = field(default_factory=list)  # due reminders, spoken by the main loop when idle
    history: list = field(default_factory=list)  # (time, user text, reply) for follow-ups
    turn: list = field(default_factory=list)  # actions done so far in the current request
    app_words: list = field(default_factory=list)  # installed app names, given to Whisper as hotwords
    show: Callable = lambda **fields: None  # overlay updates (emotion); ui.set in the real app
    route: str = ""  # how the last request was handled (shortcut, pattern, llm, ...), for History


def dictation_text(raw):
    """What voice typing types: Whisper's text as is (capitals, punctuation), minus noise phantoms."""
    text = raw.strip()
    return text + " " if router.normalize(text) else ""


EMOTIONS = router.EMOTIONS


def split_emotion(reply):
    """"[happy] Sure!" -> ("happy", "Sure!"). The tag drives the face and is never spoken."""
    m = re.match(r"\s*\[(\w+)\]\s*", reply or "")
    if not m:
        return "neutral", (reply or "").strip()
    emotion = m[1].lower()
    return (emotion if emotion in EMOTIONS else "neutral"), reply[m.end():].strip()


def choose_emotion(emotion, reply):
    """The model's tag, except that a question back to the user shows interest instead of a blank face."""
    if emotion == "neutral" and reply.rstrip().endswith("?"):
        return "curious"
    return emotion


def fact_words(facts):
    """Names you told Zade ("my name is zonic") as hotwords, so Whisper stops hearing "Sonic"."""
    out = []
    for f in facts:
        for m in re.finditer(r"(?:name is|is called|called) (\w+)", f, re.IGNORECASE):
            out.append(m[1].capitalize())
    return list(dict.fromkeys(out))


def next_time(at, now=None):
    """Next occurrence of a clock time like "17:00", "5 pm" or "9:30 am"."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?\s*m?\.?\s*", at.lower())
    if not m:
        raise ValueError(f"I don't understand the time {at}.")
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3] == "p" and hour < 12:
        hour += 12
    if m[3] == "a" and hour == 12:
        hour = 0
    now = now or datetime.datetime.now()
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t if t > now else t + datetime.timedelta(days=1)


def is_quiet(cfg, now=None):
    """Do Not Disturb, or inside the quiet-hours window (which may wrap past midnight)."""
    q = cfg["quiet"]
    if q["dnd"]:
        return True
    if not q["enabled"]:
        return False
    now = now or datetime.datetime.now()
    minutes = now.hour * 60 + now.minute
    start, end = (int(t.split(":")[0]) * 60 + int(t.split(":")[1]) for t in (q["start"], q["end"]))
    return start <= minutes < end if start <= end else minutes >= start or minutes < end




def apply_live(cfg, new):
    for section in config.LIVE_SECTIONS:
        cfg[section] = new[section]
    for key in config.LIVE_KEYS:  # read on every request, so they can change live
        table, name = key.split(".")
        cfg[table][name] = new[table][name]


# Actions that need a spoken yes at each safety level (shell and power always ask, in actions.py).
RISKY = {"close_app", "type_text", "clipboard_copy", "press_keys"}
READ_ONLY = {"express", "dnd", "time", "date", "weather", "web_answer", "notes_read", "list_facts", "list_reminders",
             "system_status", "look_at_screen", "clipboard_read", "remember", "forget", "note_add",
             "set_timer", "set_reminder", "cancel_reminder", "make_shortcut", "sync_apps", "sleep",
             "shell", "power"}


def needs_confirm(name, level, args=None):
    if level == "everything":
        return name not in READ_ONLY
    if level == "risky":
        return name in RISKY or (name == "window" and (args or {}).get("action") == "close")
    return False


def pump_reminders(ctx, now=None):
    """Move due reminders into ctx.alerts (the main loop speaks them when idle). Returns how many.
    During quiet hours they wait, and are spoken once quiet time ends."""
    if is_quiet(ctx.cfg):
        return 0
    due = memory.due_reminders(ctx.conn, time.time() if now is None else now)
    ctx.alerts.extend(due)
    return len(due)


def recent(ctx, now=None):
    f = ctx.cfg["followup"]
    now = time.monotonic() if now is None else now
    return [(u, r) for t, u, r in ctx.history if now - t <= f["history_s"]][-f["history_turns"]:]


def wants_followup(reply):
    return bool(reply) and reply.rstrip().endswith("?")


def dispatch(ctx, action, from_model=False):
    name, a = action["name"], action.get("args", {})
    try:
        # Closing things on the model's own initiative always needs a yes; the user naming it doesn't.
        model_close = from_model and (name == "close_app" or (name == "window" and a.get("action") == "close")
                                      or (name == "press_keys" and actions.is_closing(a.get("keys", ""))))
        if model_close or needs_confirm(name, ctx.cfg["safety"]["confirm"], a):
            detail = next((str(v) for v in a.values() if isinstance(v, (str, int))), "")
            if not ctx.confirm(f"{name.replace('_', ' ').capitalize()}{' ' + detail if detail else ''}?"):
                return "Cancelled.", False
        if name == "remember":
            memory.add_fact(ctx.conn, a["fact"])
            return "Got it.", True
        if name == "forget":
            return ("Forgotten." if memory.forget_fact(ctx.conn, a["query"]) else "I didn't know that."), True
        if name == "list_facts":
            f = memory.facts(ctx.conn)
            return ("I know that " + "; ".join(f) + "." if f else "I don't know anything about you yet."), True
        if name == "make_shortcut":
            last = ctx.turn or memory.last_actions(ctx.conn)  # "when I say X, do Y" saves this turn's Y
            if not last:
                return "There's nothing to save yet.", False
            memory.add_shortcut(ctx.conn, router.normalize(a["phrase"]), last)
            return f"Saved. Say {a['phrase']} any time.", True
        if name == "sleep":
            brain.unload(ctx.cfg)
            return "Going to sleep.", True
        if name == "play_music":
            from . import music

            return music.play(a["query"], ctx.cfg["music"]["mode"],
                              a.get("provider") or ctx.cfg["music"]["provider"], a.get("device", ""),
                              ctx.cfg["music"]["play_on"], fix=lambda q: brain.fix_song(q, ctx.cfg)), True
        if name == "look_at_screen":
            from . import vision

            return vision.look(a.get("question") or "What's on the screen?", ctx.cfg), True
        if name == "express":  # the face itself is set by handle(); this is what Zade says
            e = a.get("emotion", "")
            if e not in EMOTIONS:
                return "I can make these faces: " + ", ".join(EMOTIONS) + ".", False
            return f"This is my {e} face.", True
        if name == "dnd":
            from . import ctl

            ctx.cfg["quiet"]["dnd"] = bool(a.get("on", True))
            ctl.set_setting("quiet.dnd", "true" if ctx.cfg["quiet"]["dnd"] else "false")
            return ("Do not disturb is on. Hold Win to talk to me." if ctx.cfg["quiet"]["dnd"]
                    else "Do not disturb is off."), True
        if name == "system_status":
            from . import system

            return system.status(a.get("what", "all")), True
        if name == "sync_apps":
            ctx.app_words[:] = actions.app_names()
            return f"Synced {actions.all_app_count()} apps.", True
        if name == "weather":
            return info.weather(a.get("place") or ctx.cfg["weather"]["place"], a.get("day", 0)), True
        if name == "web_search":
            a = {**a, "engine": ctx.cfg["web"].get("engine", "google")}
            return ctx.run_action({"name": name, "args": a}, ctx.confirm), True
        if name == "web_answer":
            return info.web_answer(a["query"], ctx.cfg), True
        if name == "note_add":
            return info.note_add(a["text"], ctx.cfg), True
        if name == "notes_read":
            return info.notes_read(ctx.cfg), True
        if name == "set_timer":
            seconds, message = int(a["seconds"]), a.get("message") or "Time's up."
            memory.add_reminder(ctx.conn, time.time() + seconds, message)
            return f"Timer set for {_duration(seconds)}.", True
        if name == "set_reminder":
            t = next_time(a["at"])
            daily = bool(a.get("daily"))
            memory.add_reminder(ctx.conn, t.timestamp(), a["message"], 86400 if daily else None)
            when = f"{t:%-I:%M %p}"
            if daily:
                return f"Every day at {when} I'll remind you to {a['message']}.", True
            return f"At {when}{' tomorrow' if t.date() > datetime.date.today() else ''} I'll remind you to {a['message']}.", True
        if name == "list_reminders":
            rows = memory.reminders(ctx.conn)
            if not rows:
                return "You have no reminders.", True
            items = [f"{m} {'every day at' if r else 'at'} {datetime.datetime.fromtimestamp(d):%-I:%M %p}"
                     for d, m, r in rows]
            return "Your reminders: " + "; ".join(items) + ".", True
        if name == "cancel_reminder":
            n = memory.cancel_reminder(ctx.conn, a["query"])
            return (f"Cancelled {n} reminder{'s' if n != 1 else ''}." if n else "I found no reminder like that."), n > 0
        return ctx.run_action(action, ctx.confirm), True
    except actions.Failed as e:
        return str(e), False
    except Exception as e:  # a bad tool call must become an error the LLM sees, never a crash
        log.warning("action %s failed: %s", action, e)
        return f"That failed: {e}", False


def offer(ctx, text, acts):
    if memory.should_offer(ctx.conn, text, acts, ctx.cfg["learning"]["promote_after"]):
        if ctx.confirm(f"Want '{text}' to always do that?"):
            memory.add_shortcut(ctx.conn, text, acts)
            ctx.say("Saved.")
        else:
            memory.decline(ctx.conn, text, acts)


def handle(ctx, raw):
    """Handle one utterance; return what Zade replied (for follow-up listening)."""
    text = router.normalize(raw)
    if text in STOP_WORDS:
        return ""
    if taught := router.parse_teach(text):
        phrase, request = taught
        handle(ctx, request)
        if ctx.turn:
            memory.add_shortcut(ctx.conn, phrase, ctx.turn)
            reply = f"Got it. Say {phrase} any time."
        else:
            reply = "That didn't work, so I didn't save it."
        ctx.say(reply)
        return reply
    ctx.turn = []
    ctx.route = "llm"
    r = router.route(text, memory.shortcuts(ctx.conn), ctx.cfg["router"], ctx.predict, ctx.find_app)
    if r.kind == "none":  # nothing (or only noise) was said after the wake word: stay silent
        return ""
    if r.kind == "confirm" and not ctx.confirm(f"Did you mean {r.label}?"):
        r = router.Route("llm")
    if r.kind in ("run", "confirm"):
        ctx.route = r.source
        results = [dispatch(ctx, a) for a in r.actions]
        ok = all(k for _, k in results)
        ctx.turn = [a for a, (_, k) in zip(r.actions, results) if k and a["name"] not in MEMORY_TOOLS]
        reply = " ".join(t for t, _ in results if t) or "Done."
        shown = next((a["args"]["emotion"] for a in r.actions if a["name"] == "express" and a["args"]["emotion"]), None)
        ctx.show(emotion=shown or ("happy" if ok else "sad"))
        ctx.say(reply)
        if r.phrase:
            memory.use_shortcut(ctx.conn, r.phrase)
        learnable = not any(a["name"] in MEMORY_TOOLS for a in r.actions)
        memory.log(ctx.conn, text, r.actions, r.source, ok)
        if ok and learnable and not r.phrase:
            offer(ctx, text, r.actions)
    else:
        executed = []

        def run_tool(name, args):
            out, ok = dispatch(ctx, {"name": name, "args": args}, from_model=True)
            if ok and name not in MEMORY_TOOLS:
                executed.append({"name": name, "args": args})
                ctx.turn.append({"name": name, "args": args})
            return out or "done"

        emotion, reply = split_emotion(ctx.ask(text, memory.facts(ctx.conn), ctx.cfg, run_tool, recent(ctx)))
        ctx.show(emotion=choose_emotion(emotion, reply))
        ctx.say(reply)
        if executed:
            memory.log(ctx.conn, text, executed, "llm", True)
            offer(ctx, text, executed)
    ctx.history.append((time.monotonic(), text, reply))
    del ctx.history[:-20]
    return reply


def single_instance(path):
    """Hold an exclusive lock for as long as this Zade runs; None if another Zade already holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def read_inbox(path):
    """Commands typed in the desktop app, one per line; the file is emptied once read. The app appends
    under the same lock, so a command written while this runs lands before or after, never lost."""
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            lines = f.read().splitlines()
            f.truncate(0)
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip()]


def safe_handle(ctx, text):
    t = time.perf_counter()
    ctx.route = ""
    try:
        reply = handle(ctx, text)
        if ctx.route or reply:  # silence and "stop" are not worth a history entry
            memory.log_request(ctx.conn, text.strip(), reply, ctx.route or "none",
                               (time.perf_counter() - t) * 1000, ctx.cfg["history"]["keep"])
        return reply
    except Exception:  # one bad request must not kill the assistant
        log.exception("handling %r failed", text)
        try:
            ctx.say("Something went wrong.")
        except Exception:
            log.exception("could not report the failure")
        return ""


def laya_predictor(cfg):
    if not cfg["router"]["laya_enabled"]:
        return None
    try:
        import laya

        return laya.load("convaiinnovations/laya").predict
    except Exception as e:  # missing package, download failure or bad checkpoint: run without Laya
        log.warning("Laya unavailable, skipping: %s", e)
        return None


def main():
    from . import audio, stt, tts, ui

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    lock = single_instance(pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.lock")
    if lock is None:
        log.error("Zade is already running; not starting a second copy.")
        sys.exit(1)
    config.load_env()  # API keys (Spotify, cloud models) from ~/.config/zade/env
    try:
        conn = memory.connect(pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.db")
    except sqlite3.DatabaseError as e:
        log.error("database unusable, learning disabled this session: %s", e)
        conn = memory.connect(":memory:")
    stream = audio.open_stream()
    wake = audio.wake_model(cfg)
    # Hotkey: `kill -USR1 $(cat ~/.local/share/zade/zade.pid)` acts like saying the wake word.
    trigger = threading.Event()
    signal.signal(signal.SIGUSR1, lambda *_: trigger.set())
    pid_file = pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.pid"
    pid_file.write_text(str(os.getpid()))
    from . import hotkey

    dictate = threading.Event()
    bindings = [(cfg["hotkey"]["key"], cfg["hotkey"]["hold_s"], trigger) if cfg["hotkey"]["enabled"] else None,
                (cfg["dictation"]["key"], cfg["dictation"]["hold_s"], dictate) if cfg["dictation"]["enabled"] else None]
    detectors = iter(hotkey.watch_all([b for b in bindings if b]))
    ptt = next(detectors) if bindings[0] else None    # push-to-talk key detector
    typer = next(detectors) if bindings[1] else None  # voice-typing key detector

    def hear(timeout=None, released=None, cancelled=None, keep_reply=False):
        stt.gpu_start(cfg)  # the GPU speech model (if used) loads while the user talks
        # keep_reply: while answering a question, keep it (e.g. a command to approve) on screen
        ui.show("listening", heard="", emotion="neutral", **({} if keep_reply else {"reply": ""}))
        a = audio.record(stream, cfg, timeout, released, cancelled, on_level=ui.level)
        if a is None:
            ui.show("idle")
            return None
        ui.show("thinking")
        words = [*memory.shortcuts(conn), *fact_words(memory.facts(conn)), *ctx.app_words]
        text = stt.transcribe(a, cfg, hotwords=words)
        ui.show("thinking", heard=text.strip())
        return text

    spoke_at = []
    barge = []  # why speech was interrupted ("wake" or "hotkey"); empty when not interrupted

    def interrupted():
        """While speaking: stop if the hotkey fires or the wake word is heard (barge-in)."""
        if trigger.is_set():
            trigger.clear()
            barge.append("hotkey")
            return True
        while stream.read_available >= audio.FRAME:
            if max(wake.predict(audio.read(stream)).values()) >= cfg["wake"]["threshold"]:
                barge.append("wake")
                return True
        return False

    def say(text):
        if barge:  # the user cut in: stay quiet for the rest of this request
            return
        spoke_at.append(time.perf_counter())
        wake.reset()
        spoken, _, detail = text.partition("\n")  # text after a newline is shown, not spoken
        ui.show("speaking", reply=tts.clean(spoken) + (f"\n{detail}" if detail else ""))
        if not tts.speak(spoken, cfg, interrupt=interrupted):
            audio.drain(stream)
        ui.show("done")

    def confirm(question):
        say(question)
        if barge:  # interrupted instead of answering: treat as no
            return False
        return actions.is_yes(hear(5.0, keep_reply=True) or "")

    ctx = Ctx(cfg, conn, say, confirm, predict=laya_predictor(cfg))
    ctx.show = ui.set
    ctx.app_words = actions.app_names()  # your installed apps and games, so Whisper expects their names
    overlay = ui.start(cfg)
    if overlay:
        atexit.register(overlay.terminate)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run exit handlers (close the overlay) on stop

    last_check = [0.0]
    typed = []  # commands typed in the desktop app
    inbox = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "zade" / "inbox"

    config_file = pathlib.Path("~/.config/zade/config.toml").expanduser()
    config_mtime = [config_file.stat().st_mtime if config_file.exists() else 0]

    def poll():
        if time.monotonic() - last_check[0] >= 0.5:  # reminders, typed commands, live settings
            last_check[0] = time.monotonic()
            pump_reminders(ctx)
            stt.gpu_idle(cfg)  # frees its VRAM after stt.keep_alive_s
            typed.extend(read_inbox(inbox))
            mtime = config_file.stat().st_mtime if config_file.exists() else 0
            if mtime != config_mtime[0]:  # changed in the app: overlay, sounds, quiet hours, safety apply now
                config_mtime[0] = mtime
                try:
                    apply_live(cfg, config.load())
                    ui.configure(cfg)
                    log.info("live settings reloaded")
                except Exception as e:  # a half-written or broken file: keep the current settings
                    log.warning("could not reload settings: %s", e)
        if typed:
            return "typed"
        if ctx.alerts:
            return "alert"
        if trigger.is_set():
            trigger.clear()
            return "hotkey"
        if dictate.is_set():
            dictate.clear()
            return "dictate"
        return None

    def respond(text):
        t = time.perf_counter()
        spoke_at.clear()
        reply = safe_handle(ctx, text)
        if not spoke_at:  # stayed silent (nothing said, or "stop"): hide the overlay
            ui.show("idle")
        log.info("heard %r, replied after %.2fs (speech threshold %d)", text,
                 (spoke_at[0] if spoke_at else time.perf_counter()) - t,
                 audio.speech_threshold(audio.noise, cfg["audio"]["rms_threshold"], cfg["audio"]["noise_factor"]))
        return reply

    stt.transcribe(np.zeros(audio.RATE, np.int16), cfg)  # load whisper before the first command
    stt.gpu_start(cfg)  # counts as a use, so a GPU model loaded at login is freed after the idle time
    if cfg["wake"]["verify"]:
        stt.wake_check(np.zeros(audio.RATE, np.int16), cfg)  # and the wake word's second-opinion model
    log.info("ready")
    (pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.ready").write_text(str(os.getpid()))  # for the app
    while True:
        if barge:  # interrupted mid-speech: listen right away, as if woken
            source = barge.pop()
            barge.clear()
        else:
            source = audio.wait_for_wake(stream, wake, cfg["wake"]["threshold"], poll,
                                         verify=(lambda clip: stt.wake_check(clip, cfg)) if cfg["wake"]["verify"] else None)
        if source == "wake" and is_quiet(cfg):  # quiet hours / Do Not Disturb: ignore the wake word
            log.info("wake word ignored (quiet)")
            continue
        if source == "dictate":  # voice typing: record while the key is held, type it, no model
            audio.cue(stream, soft=True, cfg=cfg)
            text = hear(released=lambda: not typer.held(), cancelled=typer.cancelled)
            if text and (typed := dictation_text(text)):
                actions._call(["wtype", "--", typed])
                log.info("typed %r", typed)
            ui.show("idle")
            continue
        if source == "typed":  # from the app: no microphone, straight to handling
            text = typed.pop(0)
            ui.show("thinking", heard=text, reply="")
            respond(text)
            if not spoke_at:
                ui.show("idle")
            continue
        if source == "alert":
            while ctx.alerts and not barge:  # cut in: the rest wait until the user is done
                say("Reminder: " + ctx.alerts.pop(0))
            continue
        audio.cue(stream, cfg=cfg)
        threading.Thread(target=brain.warm_up, args=(cfg,), daemon=True).start()
        # Push-to-talk: while the key is still held, record until it is released.
        ptt_active = source == "hotkey" and ptt and ptt.held()
        if cfg["sound"]["wake_reply"] and not ptt_active:  # e.g. "Yes?" before listening
            tts.speak(cfg["sound"]["wake_reply"], cfg)
            audio.drain(stream)
        text = hear(released=(lambda: not ptt.held()) if ptt_active else None,
                    cancelled=ptt.cancelled if ptt_active else None)
        if text is None:
            audio.drain(stream)
            continue
        reply = respond(text)
        # Follow-up: when Zade asked a question, listen briefly for an answer without the wake word.
        while cfg["followup"]["enabled"] and wants_followup(reply) and not barge:
            audio.cue(stream, soft=True, cfg=cfg)
            text = hear(cfg["followup"]["listen_s"])
            if text is None:
                break
            reply = respond(text)


if __name__ == "__main__":
    main()

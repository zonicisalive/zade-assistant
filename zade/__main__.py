import logging
import pathlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import actions, brain, config, memory, router

log = logging.getLogger("zade")
MEMORY_TOOLS = {"remember", "forget", "list_facts", "make_shortcut", "sleep"}


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


def dispatch(ctx, action):
    name, a = action["name"], action.get("args", {})
    try:
        if name == "remember":
            memory.add_fact(ctx.conn, a["fact"])
            return "Got it.", True
        if name == "forget":
            return ("Forgotten." if memory.forget_fact(ctx.conn, a["query"]) else "I didn't know that."), True
        if name == "list_facts":
            f = memory.facts(ctx.conn)
            return ("I know that " + "; ".join(f) + "." if f else "I don't know anything about you yet."), True
        if name == "make_shortcut":
            last = memory.last_actions(ctx.conn)
            if not last:
                return "There's nothing to save yet.", False
            memory.add_shortcut(ctx.conn, router.normalize(a["phrase"]), last)
            return f"Saved. Say {a['phrase']} any time.", True
        if name == "sleep":
            brain.unload(ctx.cfg)
            return "Going to sleep.", True
        return ctx.run_action(action, ctx.confirm), True
    except actions.Failed as e:
        return str(e), False
    except (KeyError, TypeError, ValueError, OSError) as e:
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
    text = router.normalize(raw)
    r = router.route(text, memory.shortcuts(ctx.conn), ctx.cfg["router"], ctx.predict, ctx.find_app)
    if r.kind == "none":
        ctx.say("Sorry, didn't catch that.")
        return
    if r.kind == "confirm" and not ctx.confirm(f"Did you mean {r.label}?"):
        r = router.Route("llm")
    if r.kind in ("run", "confirm"):
        results = [dispatch(ctx, a) for a in r.actions]
        ok = all(k for _, k in results)
        ctx.say(" ".join(t for t, _ in results if t) or "Done.")
        if r.phrase:
            memory.use_shortcut(ctx.conn, r.phrase)
        memory.log(ctx.conn, text, r.actions, r.source, ok)
        if ok and not r.phrase:
            offer(ctx, text, r.actions)
        return
    executed = []

    def run_tool(name, args):
        out, ok = dispatch(ctx, {"name": name, "args": args})
        if ok and name not in MEMORY_TOOLS:
            executed.append({"name": name, "args": args})
        return out or "done"

    ctx.say(ctx.ask(text, memory.facts(ctx.conn), ctx.cfg, run_tool))
    if executed:
        memory.log(ctx.conn, text, executed, "llm", True)
        offer(ctx, text, executed)


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
    from . import audio, stt, tts

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    try:
        conn = memory.connect(pathlib.Path(cfg["paths"]["data"]).expanduser() / "zade.db")
    except sqlite3.DatabaseError as e:
        log.error("database unusable, learning disabled this session: %s", e)
        conn = memory.connect(":memory:")
    stream = audio.open_stream()
    wake = audio.wake_model(cfg)

    def hear(timeout=None):
        a = audio.record(stream, cfg, timeout)
        return None if a is None else stt.transcribe(a, cfg, prompt=", ".join(memory.shortcuts(conn)))

    def say(text):
        tts.speak(text, cfg)
        audio.drain(stream)

    def confirm(question):
        say(question)
        return actions.is_yes(hear(5.0) or "")

    ctx = Ctx(cfg, conn, say, confirm, predict=laya_predictor(cfg))
    stt.transcribe(np.zeros(audio.RATE, np.int16), cfg)  # load whisper before the first command
    log.info("ready")
    while True:
        audio.wait_for_wake(stream, wake, cfg["wake"]["threshold"])
        audio.chime()
        threading.Thread(target=brain.warm_up, args=(cfg,), daemon=True).start()
        text = hear()
        if text is None:
            audio.drain(stream)
            continue
        t = time.perf_counter()
        handle(ctx, text)
        log.info("heard %r, handled in %.2fs", text, time.perf_counter() - t)


if __name__ == "__main__":
    main()

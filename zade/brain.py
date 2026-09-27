import datetime
import json
import logging
import pathlib
import re

import httpx
import ollama

from . import providers

log = logging.getLogger("zade")

SYSTEM = (
    "You are {name}, a voice assistant on the user's Arch Linux desktop. Your replies are spoken "
    "aloud, so answer in at most three short sentences of plain text with no markdown and no emoji, unless "
    "the user asks for detail. Answer only what was asked: no greetings, no introducing yourself, no offers "
    "of more help, and no question at the end unless you can't go on without the answer. If the user is "
    "waving you off, telling you to stop, saying they need nothing, or clearly talking to someone else and not "
    "to you, reply with only the word SILENT. Use the tools to act on the computer. Use shell only when no other tool "
    "fits; the user approves each command, and sudo is never allowed. Only close apps or windows the user "
    "names; never close, kill or clean up apps on your own judgement. Never say you did or will do something "
    "unless you call the tool for it. When the user states a lasting "
    "fact about themselves, call remember. For news, sports results, prices, recent events or anything that "
    "may have changed after your training, call web_answer instead of answering from memory. To write text "
    "into the current window, call type_text. If open_app cannot find an app, tell the user and pass on "
    "its suggestion; never open a web search for it instead. When the user teaches a command (\"when I say X, do Y\"), "
    "do Y with tools first, then call make_shortcut with phrase X. "
    "The user speaks Indian English, where 'X is what?' means 'what is X?': 'my name is what?' asks the user's "
    "name (answer 'Your name is ...'), 'your name is what?' asks your name, 'this is what?' means "
    "'what is this?', and 'do one thing' introduces a request."
)
S = {"type": "string"}


def _t(name, description, required=(), /, **props):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}


# Kept short: all of this is sent with every request. Confirmations and safety are enforced in code, not here.
TOOLS = [
    _t("open_app", "Open an app.", ["name"], name=S),
    _t("close_app", "Close an app.", ["name"], name=S),
    _t("volume", "Speaker volume: delta % or set 0-100.", delta={"type": "integer"}, set={"type": "integer"}),
    _t("mute", "Mute or unmute the speakers.", ["action"], action={"type": "string", "enum": ["mute", "unmute"]}),
    _t("media", "Media playback.", ["cmd"], cmd={"type": "string", "enum": ["play-pause", "play", "pause", "next", "previous"]}),
    _t("web_search", "Open a web search in the browser.", ["query"], query=S),
    _t("lock_screen", "Lock the screen."),
    _t("time", "Current time."),
    _t("date", "Today's date."),
    _t("shell", "Run a shell command (no sudo).", ["cmd"], cmd=S),
    _t("remember", "Store a lasting fact about the user.", ["fact"], fact=S),
    _t("forget", "Forget a stored fact.", ["query"], query=S),
    _t("list_facts", "What is known about the user."),
    _t("make_shortcut", "Save the last request's actions under a phrase.", ["phrase"], phrase=S),
    _t("sleep", "Unload the language model."),
    _t("weather", "Weather. place: city, empty = here. day: 0 today, 1 tomorrow.", place=S, day={"type": "integer"}),
    _t("web_answer", "Search the web to answer news, recent or uncertain facts.", ["query"], query=S),
    _t("set_timer", "Timer or reminder in seconds from now.", ["seconds"], seconds={"type": "integer"}, message=S),
    _t("note_add", "Save a note.", ["text"], text=S),
    _t("notes_read", "Read saved notes."),
    _t("window", "Windows and workspaces.", ["action"], action={"type": "string", "enum": [
        "close", "fullscreen", "maximize", "focus_left", "focus_right", "overview", "workspace", "move_to_workspace"]},
       workspace=S),
    _t("open_website", "Open a website by name or domain.", ["site"], site=S),
    _t("press_keys", "Press keys, e.g. \"ctrl+c\", \"enter\"; several comma-separated.", ["keys"], keys=S),
    _t("clipboard_read", "Read the clipboard."),
    _t("clipboard_copy", "Copy text to the clipboard.", ["text"], text=S),
    _t("discord", "Discord: mic, deafen, voice channels, calls, chats, messages, reactions, status, summaries. "
       "Empty target = the open chat.",
       ["action"], action={"type": "string", "enum": ["mute", "unmute", "deafen", "undeafen", "leave", "join", "call",
                                                      "open", "read", "unread", "status", "react", "unreact", "reply",
                                                      "edit", "delete", "set_status", "summarize"]},
       target={"type": "string", "description": "person, or channel with its server as said: \"staff-vc in BITNADE\""},
       text={"type": "string", "description": "reply/edit text, or status: online, idle, dnd, invisible"},
       emoji={"type": "string", "description": "emoji character"}, count={"type": "integer"}),
    _t("send_message", "Message someone on Discord. to \"the current chat\" = the open chat; text \"the screenshot\" "
       "sends the latest screenshot.", ["to", "text"], to=S, text=S),
    _t("type_text", "Type text into the focused window.", ["text"], text=S),
    _t("brightness", "Screen brightness (dim/brighten): delta % or set 0-100.", set={"type": "integer"}, delta={"type": "integer"}),
    _t("screenshot", "Take a screenshot."),
    _t("play_music", "Play a song, artist or album. provider/device only if named.", ["query"],
       query=S, provider={"type": "string", "enum": ["spotify", "youtube", "youtube music"]}, device=S),
    _t("look_at_screen", "Look at the screen and answer about it.", ["question"], question=S),
    _t("set_reminder", "Reminder at a clock time (\"17:00\"); daily repeats.", ["message", "at"], message=S, at=S,
       daily={"type": "boolean"}),
    _t("list_reminders", "List reminders."),
    _t("cancel_reminder", "Cancel a reminder.", ["query"], query=S),
    _t("snooze", "Ignore the wake word for seconds (0 ends it).", ["seconds"], seconds={"type": "integer"}),
    _t("dnd", "Zade's Do Not Disturb on/off.", ["on"], on={"type": "boolean"}),
    _t("system_status", "CPU/GPU temperature and load, memory.", ["what"],
       what={"type": "string", "enum": ["all", "cpu", "gpu", "ram"]}),
    _t("sync_apps", "Rescan installed apps."),
    _t("power", "Suspend, reboot, shut down or log out.", ["action"],
       action={"type": "string", "enum": ["suspend", "reboot", "shutdown", "logout"]}),
]


def vram_free_gb(root="/sys/class/drm"):
    best = None
    for dev in pathlib.Path(root).glob("card*/device"):
        t, u = dev / "mem_info_vram_total", dev / "mem_info_vram_used"
        if t.exists() and u.exists():
            total, used = int(t.read_text()), int(u.read_text())
            if best is None or total > best[0]:
                best = (total, used)
    return (best[0] - best[1]) / 2**30 if best else None


def resident_on_gpu(cfg):
    """True when our model is already loaded on the GPU (e.g. by warm_up), so its own VRAM use does not count."""
    try:
        return any(m.model == cfg["llm"]["model"] and m.size_vram
                   for m in ollama.Client(host=cfg["llm"]["host"]).ps().models)
    except (ollama.ResponseError, httpx.HTTPError, ConnectionError):
        return False


def candidates(cfg, vram=vram_free_gb, resident=resident_on_gpu):
    llm, out = cfg["llm"], []
    if llm["provider"] == "ollama":
        free = vram()
        if free is None or free >= llm["vram_min_free_gb"] or resident(cfg):
            out.append(("ollama", {}))
    else:
        out.append((llm["provider"], {}))
    fb = llm["fallback"]
    if fb == "cpu":
        out.append(("ollama", {"num_gpu": 0}))
    elif fb not in ("none", "", llm["provider"]):
        out.append((fb, {}))
    return out


# Asking for it: "think carefully", "think about...", "step by step", "soch ke batao"
ASKED_TO_THINK = re.compile(r"\b(?:think|thinking|reason (?:it|this) out|step by step|carefully|soch(?:\s?ke|\s?kar)?)\b", re.I)


# Measured on six trick questions with qwen3:8b: still 6 of 6 right, with ~40% less thinking (4.3 s instead
# of 6.9 s on average, 6.7 s instead of 12.3 s for the slowest).
THINK_BRIEFLY = ("\nThink briefly: only the few steps the question really needs, check the result once, then answer. "
                 "Don't restate the question or explore alternatives you won't use.")


def should_think(text, cfg):
    """Whether this request gets thinking (llm.thinking: off | ask | auto | always)."""
    mode = cfg["llm"].get("thinking", "off")
    if mode == "always":
        return True
    if mode not in ("ask", "auto"):
        return False
    return bool(ASKED_TO_THINK.search(text)) or \
        (mode == "auto" and len(text.split()) >= cfg["llm"].get("think_min_words", 25))


def ask(text, facts, cfg, run_tool, history=(), vram=vram_free_gb, resident=resident_on_gpu):
    now = datetime.datetime.now().astimezone()
    system = SYSTEM.replace("{name}", cfg["persona"]["name"] or "Zade") + (
        "\nBegin every reply with exactly one emotion tag that fits it: "
        "[neutral] [happy] [excited] [laughing] [love] [sad] [crying] [confused] [surprised] [amazed] [annoyed] "
        "[angry] [curious] [smug] [sleepy] [embarrassed] [nervous] [wink] [playful]. "
        "The tag is shown on your face, never spoken. Be expressive: a greeting or friendly chat is [happy]; "
        "a joke is [laughing] or [playful]; good news is [excited]; bad news is [sad]; compliments or affection "
        "are [love] or [embarrassed]; being tired or saying good night is [sleepy]; an action that failed is "
        "[embarrassed]; a question back to the user is [curious]; an insult is [sad] or [annoyed]. "
        "Use [neutral] only for dry facts.") + (f"\nNow: {now:%A %Y-%m-%d %H:%M}, the user's local time "
                       f"(time zone {now:%Z}, UTC{now:%z}). Use it as is; do not convert it.")
    if cfg["llm"].get("personality"):
        system += "\nThe user's instructions for your personality and style: " + cfg["llm"]["personality"]
    if facts:
        system += "\nKnown facts about the user:\n" + "\n".join(f"- {f}" for f in facts)
    ran, seen = [], {}

    def tracked(name, args):
        # Small models sometimes repeat a call each round: "volume +10" four times is +40. The same call
        # again in one request gets the first result, without doing it twice.
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in seen:
            return seen[key]
        result = seen[key] = run_tool(name, args)
        ran.append((name, result))
        return result

    for name, extra in candidates(cfg, vram, resident):
        try:
            think = should_think(text, cfg) and extra.get("num_gpu") != 0  # not on the slow CPU fallback
            reply = providers.chat(name, system + (THINK_BRIEFLY if think else ""), text, TOOLS, tracked, cfg, extra,
                                   history, **({"think": True} if think else {}))
            if leaked := leaked_call(reply):  # the call written out as text instead of made: make it (confirmations apply)
                result = tracked(*leaked)
                return result if result and result != "done" else "Done."
            # The model tends to paraphrase a music result into "I started playing <what you asked>",
            # even when a different song played or nothing did, so say what really happened.
            if [n for n, _ in ran] == ["play_music"]:
                result = str(ran[0][1])
                return ("[happy] " if result.startswith("Playing") else "[embarrassed] ") + result
            return reply
        except providers.ProviderError as e:
            log.warning("LLM %s %s failed: %s", name, extra, e)
            if ran:  # retrying elsewhere would repeat actions that already happened
                return "I did part of that, then lost my connection."
    return "My brain is offline right now."


def _loads(text):
    """JSON, also when a small model wrote Python-style escapes ("\\U0001f600" for an emoji)."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(re.sub(r"\\U([0-9a-fA-F]{8})", lambda m: chr(int(m[1], 16)), text))


def leaked_call(reply):
    """(tool, args) when the reply is a tool call written as text: 'send_message {"to": ...}' or
    '{"name": "remember", "arguments": {...}}'. Small models do this now and then; None otherwise."""
    tools = {t["name"] for t in TOOLS}
    t = re.sub(r"^\s*(?:\[\w+\]\s*)?`*(?:json)?\s*|\s*`*\s*(?:\[\w+\])?\s*$", "", reply or "")
    try:
        if m := re.fullmatch(r"(\w+)\s*(\{.*\})", t, re.S):
            name, args = m[1], _loads(m[2])
        elif t.startswith("{"):
            obj = _loads(t)
            name, args = obj.get("name"), obj.get("arguments") or obj.get("parameters") or {}
        else:
            return None
    except (ValueError, AttributeError):
        return None
    return (name, args) if name in tools and isinstance(args, dict) else None


SUMMARY = ("Summarize this Discord chat for someone who missed it, in two or three short spoken sentences: who "
           "talked about what, and anything that needs their answer. Plain text, no lists, no emoji.")


def summarize(text, cfg):
    """A spoken summary of chat messages, from the first brain that answers."""
    for name, extra in candidates(cfg):
        try:
            return providers.chat(name, SUMMARY, text, [], lambda n, a: "", cfg, extra)
        except providers.ProviderError as e:
            log.warning("summary with %s failed: %s", name, e)
    return None


def pick_emoji(description, cfg):
    """The emoji that fits a word that isn't an emoji's name ("mad" -> 😠), from the first brain that answers."""
    import unicodedata

    for name, extra in candidates(cfg):
        try:
            reply = providers.chat(name, "Reply with only the one emoji character that best fits the feeling or thing "
                                   "described. Examples: mad -> \U0001f621, lol -> \U0001f602, gg -> \U0001f44d.",
                                   description, [], lambda n, a: "", cfg, {**extra, "temperature": 0})
        except providers.ProviderError:
            continue
        chars = [c for c in reply or "" if unicodedata.category(c) == "So" or c == "\ufe0f"]
        if chars:
            return "".join(chars[:2]).rstrip() if len(chars) > 1 and chars[1] == "\ufe0f" else chars[0]
    return None


FIX_SONG = ("A speech recognizer transcribed a request to play a song. It often mishears names, because the user "
            "speaks English with an Indian accent: words are replaced by similar-sounding ones. Think of famous songs "
            "and artists whose names SOUND like the words. Examples: \"lucid dreams by juice world\" -> Lucid Dreams "
            "by Juice WRLD; \"shape of you by ed shiran\" -> Shape of You by Ed Sheeran; \"lose yourself by am in em\" "
            "-> Lose Yourself by Eminem; \"tum hi ho by are it sing\" -> Tum Hi Ho by Arijit Singh. Reply with only the "
            "real song and artist as song by artist. No other words.")


def fix_song(query, cfg, vram=vram_free_gb, resident=resident_on_gpu):
    """The model's best guess at a misheard song request ("cola berry d" -> "why this kolaveri di by
    anirudh ravichander"); the request unchanged if no model answers."""
    for name, extra in candidates(cfg, vram, resident):
        try:
            out = providers.chat(name, FIX_SONG, query, [], lambda *a: "", cfg, {**extra, "num_predict": 40})
        except providers.ProviderError as e:
            log.warning("LLM %s could not fix %r: %s", name, query, e)
            continue
        lines = re.sub(r"^\[\w+\]\s*", "", (out or "").strip()).splitlines()
        first = re.split(r"\s*(?:->|→)\s*", lines[0])[0].strip().strip('"').strip() if lines else ""  # no rambling
        return first if 0 < len(first) < 120 else query
    return query


def _load(cfg, keep_alive):
    llm = cfg["llm"]
    try:
        ollama.Client(host=llm["host"]).generate(
            model=llm["model"], prompt="", keep_alive=keep_alive, options={"num_ctx": llm["num_ctx"]})
    except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as e:
        log.warning("ollama load/unload failed: %s", e)


def warm_up(cfg):
    if cfg["llm"]["provider"] != "ollama" or str(cfg["llm"]["keep_alive"]) in ("0", "0s"):
        return  # nothing to warm: with keep_alive 0 the model would unload again straight away
    free = vram_free_gb()
    if free is None or free >= cfg["llm"]["vram_min_free_gb"]:
        _load(cfg, cfg["llm"]["keep_alive"])


def unload(cfg):
    _load(cfg, 0)

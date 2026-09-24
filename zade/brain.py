import datetime
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
    "the user asks for detail. Use the tools to act on the computer. Use shell only when no other tool "
    "fits; the user approves each command, and sudo is never allowed. Only close apps or windows the user "
    "names; never close, kill or clean up apps on your own judgement. Never say you did or will do something "
    "unless you call the tool for it. When the user states a lasting "
    "fact about themselves, call remember. For news, sports results, prices, recent events or anything that "
    "may have changed after your training, call web_answer instead of answering from memory. To write text "
    "into the current window, call type_text. If open_app cannot find an app, tell the user and pass on "
    "its suggestion; never open a web search for it instead. When the user teaches a command (\"when I say X, do Y\"), "
    "do Y with tools first, then call make_shortcut with phrase X."
)
S = {"type": "string"}


def _t(name, description, required=(), /, **props):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}


TOOLS = [
    _t("open_app", "Open a desktop application by name.", ["name"], name=S),
    _t("close_app", "Close a running application by name.", ["name"], name=S),
    _t("volume", "Change speaker volume: delta in percent (negative lowers) or set to 0-100.",
       delta={"type": "integer"}, set={"type": "integer"}),
    _t("mute", "Mute (on=true) or unmute (on=false) the speakers.", ["on"], on={"type": "boolean"}),
    _t("media", "Control media playback.", ["cmd"],
       cmd={"type": "string", "enum": ["play-pause", "play", "pause", "next", "previous"]}),
    _t("web_search", "Open a web search in the browser.", ["query"], query=S),
    _t("lock_screen", "Lock the screen."),
    _t("time", "Get the current time."),
    _t("date", "Get today's date."),
    _t("shell", "Run a shell command as the user. The user must approve it. Never use sudo.", ["cmd"], cmd=S),
    _t("remember", "Store a lasting fact about the user.", ["fact"], fact=S),
    _t("forget", "Delete stored facts containing these words.", ["query"], query=S),
    _t("list_facts", "List what is known about the user."),
    _t("make_shortcut", "Save the previous request's actions under a short phrase the user chose.",
       ["phrase"], phrase=S),
    _t("sleep", "Unload the language model to free GPU memory."),
    _t("weather", "Get the weather. place is a city (empty = here); day 0 = today, 1 = tomorrow, 2 = day after.",
       place=S, day={"type": "integer"}),
    _t("web_answer", "Search the web and get the top results, to answer questions about current events or facts "
       "you are unsure of. Answer from the results in one or two sentences.", ["query"], query=S),
    _t("set_timer", "Set a timer or reminder. seconds from now; message is what to remind the user of.",
       ["seconds"], seconds={"type": "integer"}, message=S),
    _t("note_add", "Save a note for the user.", ["text"], text=S),
    _t("notes_read", "Read the user's saved notes."),
    _t("window", "Control windows and workspaces. workspace is a number or name, for workspace actions.",
       ["action"], action={"type": "string", "enum": ["close", "fullscreen", "maximize", "focus_left",
                                                      "focus_right", "overview", "workspace",
                                                      "move_to_workspace"]}, workspace=S),
    _t("open_website", "Open a website by name (youtube, github, ...) or domain.", ["site"], site=S),
    _t("press_keys", "Press keys or shortcuts in the focused window, e.g. \"ctrl+c\", \"alt+tab\", \"super+2\", "
       "\"enter\", \"f5\"; several separated by commas.", ["keys"], keys=S),
    _t("clipboard_read", "Read the text on the clipboard."),
    _t("clipboard_copy", "Copy text to the clipboard.", ["text"], text=S),
    _t("type_text", "Type text into the focused window as if typed on the keyboard.", ["text"], text=S),
    _t("brightness", "Monitor brightness: set to 0-100, or change by delta.",
       set={"type": "integer"}, delta={"type": "integer"}),
    _t("screenshot", "Take a screenshot of the screen."),
    _t("play_music", "Play a song, artist or album by name. provider and device only when the user names them "
       "(device: a speaker, phone or computer, like \"echo dot\").", ["query"],
       query=S, provider={"type": "string", "enum": ["spotify", "youtube", "youtube music"]}, device=S),
    _t("look_at_screen", "Look at the user's screen and answer a question about it (read text, errors, "
       "describe what is shown).", ["question"], question=S),
    _t("set_reminder", "Remind the user at a clock time. at is like \"17:00\" or \"5 pm\"; daily repeats it "
       "every day.", ["message", "at"], message=S, at=S, daily={"type": "boolean"}),
    _t("list_reminders", "List the user's reminders."),
    _t("cancel_reminder", "Cancel reminders whose text contains these words.", ["query"], query=S),
    _t("dnd", "Turn Do Not Disturb on or off (the wake word is ignored while on).", ["on"], on={"type": "boolean"}),
    _t("system_status", "CPU/GPU temperature and load, memory use, top process.", ["what"],
       what={"type": "string", "enum": ["all", "cpu", "gpu", "ram"]}),
    _t("sync_apps", "Rescan installed apps and games so their names are recognised."),
    _t("power", "Suspend, restart, shut down or log out. The user must confirm.", ["action"],
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
    ran = []

    def tracked(name, args):
        result = run_tool(name, args)
        ran.append((name, result))
        return result

    for name, extra in candidates(cfg, vram, resident):
        try:
            reply = providers.chat(name, system, text, TOOLS, tracked, cfg, extra, history)
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

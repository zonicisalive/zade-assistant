import datetime
import logging
import pathlib

import httpx
import ollama

from . import providers

log = logging.getLogger("zade")

SYSTEM = (
    "You are Zade, a voice assistant on the user's Arch Linux desktop. Your replies are spoken "
    "aloud, so answer in at most three short sentences of plain text with no markdown, unless the "
    "user asks for detail. Use the tools to act on the computer. Use shell only when no other tool "
    "fits; the user approves each command, and sudo is never allowed. When the user states a lasting "
    "fact about themselves, call remember. For news, sports results, prices, recent events or anything that "
    "may have changed after your training, call web_answer instead of answering from memory. To write text "
    "into the current window, call type_text."
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
    _t("mute", "Toggle mute."),
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
    _t("clipboard_read", "Read the text on the clipboard."),
    _t("clipboard_copy", "Copy text to the clipboard.", ["text"], text=S),
    _t("type_text", "Type text into the focused window as if typed on the keyboard.", ["text"], text=S),
    _t("brightness", "Monitor brightness: set to 0-100, or change by delta.",
       set={"type": "integer"}, delta={"type": "integer"}),
    _t("screenshot", "Take a screenshot of the screen."),
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
    system = SYSTEM + f"\nNow: {datetime.datetime.now():%A %Y-%m-%d %H:%M}."
    if facts:
        system += "\nKnown facts about the user:\n" + "\n".join(f"- {f}" for f in facts)
    ran = []

    def tracked(name, args):
        ran.append(name)
        return run_tool(name, args)

    for name, extra in candidates(cfg, vram, resident):
        try:
            return providers.chat(name, system, text, TOOLS, tracked, cfg, extra, history)
        except providers.ProviderError as e:
            log.warning("LLM %s %s failed: %s", name, extra, e)
            if ran:  # retrying elsewhere would repeat actions that already happened
                return "I did part of that, then lost my connection."
    return "My brain is offline right now."


def _load(cfg, keep_alive):
    llm = cfg["llm"]
    try:
        ollama.Client(host=llm["host"]).generate(
            model=llm["model"], prompt="", keep_alive=keep_alive, options={"num_ctx": llm["num_ctx"]})
    except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as e:
        log.warning("ollama load/unload failed: %s", e)


def warm_up(cfg):
    if cfg["llm"]["provider"] != "ollama":
        return
    free = vram_free_gb()
    if free is None or free >= cfg["llm"]["vram_min_free_gb"]:
        _load(cfg, cfg["llm"]["keep_alive"])


def unload(cfg):
    _load(cfg, 0)

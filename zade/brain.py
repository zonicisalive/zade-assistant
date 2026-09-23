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
    "fact about themselves, call remember."
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


def candidates(cfg, vram=vram_free_gb):
    llm, out = cfg["llm"], []
    if llm["provider"] == "ollama":
        free = vram()
        if free is None or free >= llm["vram_min_free_gb"]:
            out.append(("ollama", {}))
    else:
        out.append((llm["provider"], {}))
    fb = llm["fallback"]
    if fb == "cpu":
        out.append(("ollama", {"num_gpu": 0}))
    elif fb not in ("none", "", llm["provider"]):
        out.append((fb, {}))
    return out


def ask(text, facts, cfg, run_tool, vram=vram_free_gb):
    system = SYSTEM + f"\nNow: {datetime.datetime.now():%A %Y-%m-%d %H:%M}."
    if facts:
        system += "\nKnown facts about the user:\n" + "\n".join(f"- {f}" for f in facts)
    for name, extra in candidates(cfg, vram):
        try:
            return providers.chat(name, system, text, TOOLS, run_tool, cfg, extra)
        except providers.ProviderError as e:
            log.warning("LLM %s %s failed: %s", name, extra, e)
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

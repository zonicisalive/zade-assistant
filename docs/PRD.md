# Zade — Product Requirements Document

- Status: Draft for review
- Date: 2026-09-23
- Platform: Arch Linux desktop, AMD RX 9060 XT (16 GB), PipeWire audio

## 1. Summary

Zade is a local-first voice assistant for the desktop. You say the wake word "Zade", speak a request, and Zade either performs an action on the PC or answers out loud. Zade learns your habits over time, so frequent requests turn into instant shortcuts and a partial phrase is enough to trigger them.

Zade must be fast, private by default, and light on the GPU. It uses no VRAM while idle because the GPU is shared with other model workflows. Every processing stage (speech-to-text, LLM, text-to-speech) runs locally by default, and each one can be switched to a cloud provider in the config.

## 2. Goals

1. **Speed.** A learned shortcut or known action runs within 0.5 s of the end of speech. An LLM answer starts speaking within 2 s.
2. **Local by default, cloud when chosen.** With the default config, nothing leaves the machine and Zade works offline. Each stage can use a cloud provider instead, chosen per stage in `config.toml`.
3. **Low resource use.** 0 VRAM while idle and at most 4 GB while active. Zade never evicts or breaks other GPU workloads.
4. **Learns the user.** It remembers facts and habits and promotes repeated requests to shortcuts that trigger on partial phrases.
5. **Safe.** It never runs an unknown shell command without spoken confirmation.

## 3. Non-goals (v1)

- A GUI or tray icon
- Multiple users or speaker identification
- Continuous conversation mode (every request starts with the wake word; the confirmation reply is the only exception)
- Languages other than English
- Smart-home control, mobile apps and remote access
- Cloud providers for the wake word or the Laya router (both stay local, because they run on every utterance and must be instant)

## 4. User stories

1. "Zade, open Firefox." Firefox opens immediately without the LLM loading.
2. "Zade, volume down a bit." Volume drops by 10%.
3. "Zade, what's the difference between TCP and UDP?" Zade loads Qwen and speaks a short answer.
4. "Zade, show disk usage of my home folder." Zade answers "Run `du -sh ~`?"; you say "yes", it runs the command and reads out the result.
5. After you have asked for "start my dev setup" (which opens the terminal, editor and browser) three times, Zade asks "Want 'dev' to always mean that?" You say yes. From then on, "Zade, dev" runs it instantly.
6. "Zade, remember my projects are in ~/code." Zade stores the fact and uses it in later commands.
7. "Zade, what do you know about me?" Zade lists the stored facts. "Forget my projects folder" deletes that fact.
8. While a 12 GB model from another workflow is loaded, Zade still works: known actions stay instant, and LLM answers run on the CPU (slower) instead of competing for VRAM.

## 5. Architecture

### 5.1 Pipeline

```
 mic (sounddevice, 16 kHz mono)
   │
   ▼
 wake word (openWakeWord, CPU)  ── idle state, ~1–2% of one core
   │ detected ──────────────────────────► warm-up: preload Qwen if VRAM free (async)
   ▼
 record until silence (frame energy / RMS threshold)
   │
   ▼
 speech-to-text (faster-whisper base.en, CPU int8)
   │ text
   ▼
 router
   ├─ 1. shortcut match (rapidfuzz, score ≥ 90)            → action, instant
   ├─ 1b. pattern match ("open X", "volume to N", "search for X") → action, instant
   ├─ 2. Laya decision over known actions + shortcuts
   │      conf ≥ 0.90  → action
   │      0.60–0.90    → "Did you mean X?" → yes/no
   │      < 0.60       → step 3
   └─ 3. Qwen (Ollama, tool calling)                        → action or spoken answer
   │
   ▼
 actions (allowlist runs directly; shell commands need spoken confirmation)
   │
   ▼
 text-to-speech (Kokoro, CPU; Piper fallback) → speakers
   │
   ▼
 memory (SQLite): log history, update counts, propose shortcuts
```

### 5.2 Models and resource budget

| Stage | Model | Device | Memory |
|---|---|---|---|
| Wake word | openWakeWord, custom-trained `zade` model | CPU | ~50 MB RAM |
| End of speech | Frame energy (RMS) threshold | CPU | none |
| STT | faster-whisper `base.en`, int8 | CPU | ~200 MB RAM |
| Router | Laya (421M, `convaiinnovations/laya`), bf16 | CPU | ~0.9 GB RAM + ~0.4 GB PyTorch |
| LLM | `qwen3:4b-instruct` Q4_K_M (non-thinking), `num_ctx=4096` | GPU, CPU fallback | ~3.5 GB VRAM, only while active |
| TTS | Kokoro (`kokoro-onnx`, voice `af_heart`, speed 1.2), Piper as fallback | CPU | ~400 MB RAM |

faster-whisper (CTranslate2) has no ROCm backend, so STT runs on the CPU; `base.en` transcribes a short command in about 250 ms (measured; `small.en` took ~730 ms). Laya also runs on the CPU so that it never takes VRAM. The M0 spike (section 12) measures its latency; if it is too slow on the CPU, running it on the GPU costs under 1 GB.

The LLM choice lives in `config.toml`. `qwen2.5:3b` (~2.5 GB) is the fallback if `qwen3:4b-instruct` is too slow or unreliable in testing.

### 5.3 GPU lifecycle

- **Idle:** Qwen is not loaded, so Zade uses 0 VRAM.
- **Wake word detected:** Zade checks free VRAM by reading `/sys/class/drm/card*/device/mem_info_vram_{total,used}`.
  - If at least `vram_min_free_gb` (default 4.0) is free, it sends an empty warm-up request to Ollama so the model loads while the user is still speaking.
  - Otherwise the request is marked for CPU mode (`options.num_gpu = 0`).
- **After an LLM reply:** Zade sets `keep_alive = 60s` by default and Ollama unloads the model after 60 s idle. Setting `keep_alive = 0` in the config unloads it immediately.
- **Routed by shortcut or Laya:** Qwen is never needed. The warm-up is cheap to discard because the model unloads on the idle timer.

Ollama's server configuration should set `OLLAMA_MAX_LOADED_MODELS` to suit the user's other workflows. Zade's VRAM check exists so that Zade never triggers an Ollama eviction.

### 5.4 Cloud providers

Each stage has a `provider` setting. The default is local for all three.

| Stage | Local provider (default) | Cloud providers |
|---|---|---|
| STT | `whisper` (faster-whisper) | `openai` — any OpenAI-compatible `/audio/transcriptions` endpoint (OpenAI, Groq) |
| LLM | `ollama` | `anthropic` — Claude through the official `anthropic` Python SDK; `openai` — any OpenAI-compatible chat endpoint (OpenAI, Google Gemini's OpenAI-compatible endpoint, OpenRouter, Groq, DeepSeek, Mistral) |
| TTS | `kokoro` (Piper as fallback) | `openai` — any OpenAI-compatible `/audio/speech` endpoint |

- One `openai` provider covers most vendors: each one is just a `base_url`, an API key environment variable and a model name. Gemini goes through its OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`), so it needs no separate client.
- Claude uses the native `anthropic` SDK rather than an OpenAI-compatible shim, for reliable tool calling. Its default model in the example config is `claude-opus-5`; a faster, cheaper model such as `claude-haiku-4-5` can be set in `llm.model` if latency matters more than capability.
- All LLM providers receive the same tool definitions (section 6.3), the same facts, and the same confirmation gate. A cloud LLM never runs a shell command without the spoken yes.
- **Fallback:** `llm.fallback` decides what happens when the chosen LLM is unavailable (too little VRAM for Ollama, or no network or an API error for a cloud provider). It names another configured provider, for example `"anthropic"` to use Claude when VRAM is busy, or `"cpu"` for the local CPU fallback in 5.3. When the fallback is also unavailable, Zade says so and keeps shortcuts and Laya routes working.
- API keys are read from environment variables named in the config (`api_key_env`), for example `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`.

## 6. Functional requirements

### 6.1 Audio

- **FR-1:** Listen continuously for the wake word. The detection threshold is configurable (default 0.5).
- **FR-2:** After the wake word, play a short chime and record until 0.8 s of silence, with a maximum of 10 s.
- **FR-3:** If nothing is said within 4 s of the wake word, stop quietly.
- **FR-4:** Pause wake-word detection while Zade is speaking, so it does not wake itself.

### 6.2 Routing

- **FR-5:** Normalize the transcript (lowercase, strip punctuation and filler words such as "please", "can you" and "um").
- **FR-6:** Match against approved shortcuts using `rapidfuzz.fuzz.WRatio` plus a prefix match. A score of 90 or more runs the shortcut. This is how partial phrases work: "dev" matches "dev" and "start dev" matches "start dev setup".
- **FR-7:** Otherwise, ask Laya to choose from the list of known actions and shortcuts, with a "none of these" option. Act on its confidence using the thresholds in 5.1, which are configurable.
- **FR-8:** Otherwise, send the request to the configured LLM (Qwen through Ollama by default) with the tool definitions from 6.3 plus the stored facts from 6.4 in the system prompt.
- **FR-9:** Keep spoken answers short (at most 3 sentences) unless the user asks for more detail.

### 6.3 Actions

Allowlisted actions run without confirmation:

| Action | Implementation |
|---|---|
| `open_app(name)` | Resolve a `.desktop` entry, then `gtk-launch` / `xdg-open` |
| `close_app(name)` | `pkill -x` on a resolved process name |
| `volume(delta \| set)` | `wpctl set-volume @DEFAULT_AUDIO_SINK@` |
| `mute()` | `wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle` |
| `media(play\|pause\|next\|prev)` | `playerctl` |
| `time()`, `date()` | Python stdlib |
| `web_search(query)` | `xdg-open https://duckduckgo.com/?q=...` |
| `lock_screen()` | `loginctl lock-session` |
| `run_shortcut(id)` | Run the stored action list of an approved shortcut |
| `remember(fact)`, `forget(fact)`, `list_facts()` | Memory operations |
| `sleep()` | Unload Qwen immediately (`keep_alive=0`) |

Anything else goes through the confirmation gate:

- **FR-10:** `shell(cmd)` is available only to Qwen. Zade first reads the command back ("Run `du -sh ~`?"), then listens for about 5 s without needing the wake word. Only a clear yes ("yes", "yeah", "do it", "run it") runs the command. Anything else, including silence, cancels it.
- **FR-11:** Shell commands run through `subprocess.run(cmd, shell=True, timeout=30, capture_output=True)` as the user, never as root. `sudo` is never allowed: any command containing `sudo`, `su ` or `pkexec` is refused outright.
- **FR-12:** After the command, Zade speaks a short summary of the output (Qwen summarises output longer than 200 characters) or its exit status.

### 6.4 Memory and learning

- **FR-13:** Log every resolved request: the normalized text, the action as JSON, where it came from (shortcut, Laya or Qwen) and whether it succeeded.
- **FR-14:** When the same normalized text has resolved to the same action 3 or more times with success, and no shortcut exists for it, Zade offers: "Want '<phrase>' to always do that?" A yes creates a shortcut; a no sets a flag so the offer is never repeated.
- **FR-15:** The user can also create a shortcut explicitly: "remember 'dev' means open kitty, code and firefox".
- **FR-16:** Qwen can call `remember(fact)` when the user states a durable fact about themselves. All facts go into the Qwen system prompt, limited to 50 facts, newest first.
- **FR-17:** Approved shortcuts and allowlisted actions together form the list of choices Laya picks from.

## 7. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | End of speech to action, for a shortcut or Laya match | ≤ 0.5 s p50, ≤ 0.8 s p95 |
| NFR-2 | End of speech to first spoken word, for a Qwen answer (GPU) | ≤ 2.0 s p50 |
| NFR-3 | Same as NFR-2 but in CPU fallback | ≤ 5.0 s p50 |
| NFR-4 | Idle VRAM | 0 |
| NFR-5 | Active VRAM | ≤ 4 GB |
| NFR-6 | Idle CPU | < 3% of one core |
| NFR-7 | Network | With the default all-local config, no outbound connections at runtime (models are downloaded once at setup). A stage set to a cloud provider connects only to that provider's endpoint |
| NFR-8 | Privacy | Audio is never written to disk unless `debug.save_audio = true`. Audio leaves the machine only when `stt.provider` is a cloud provider; transcript text and stored facts leave it only when `llm.provider` is a cloud provider. API keys come from environment variables and are never stored in `config.toml` or the database |

Latency is measured with timestamps logged at each pipeline stage.

## 8. Data model (SQLite, `~/.local/share/zade/zade.db`)

```sql
CREATE TABLE history (
  id         INTEGER PRIMARY KEY,
  ts         TEXT    NOT NULL DEFAULT (datetime('now')),
  text       TEXT    NOT NULL,          -- normalized utterance
  action     TEXT    NOT NULL,          -- JSON: {"name": "open_app", "args": {...}}
  source     TEXT    NOT NULL CHECK (source IN ('shortcut','pattern','laya','llm')),
  ok         INTEGER NOT NULL
);
CREATE INDEX history_text ON history(text);

CREATE TABLE shortcuts (
  id         INTEGER PRIMARY KEY,
  phrase     TEXT    NOT NULL UNIQUE,
  actions    TEXT    NOT NULL,          -- JSON list of actions, run in order
  uses       INTEGER NOT NULL DEFAULT 0,
  created    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE declined (                 -- shortcut offers the user said no to
  text       TEXT    NOT NULL,
  action     TEXT    NOT NULL,
  PRIMARY KEY (text, action)
);

CREATE TABLE facts (
  id         INTEGER PRIMARY KEY,
  fact       TEXT    NOT NULL UNIQUE,
  created    TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

## 9. Configuration (`~/.config/zade/config.toml`)

```toml
[wake]
model = "~/.local/share/zade/zade.onnx"   # custom-trained wake word
threshold = 0.5

[stt]
provider = "whisper"            # whisper | openai
model = "base.en"
device = "cpu"

[router]
shortcut_min_score = 90
laya_accept = 0.90
laya_confirm = 0.60

[llm]
provider = "ollama"             # ollama | anthropic | openai
model = "qwen3:4b-instruct"
num_ctx = 4096
keep_alive = "60s"
vram_min_free_gb = 4.0
fallback = "cpu"                # cpu | none | name of a provider below

# Used when provider or fallback is "anthropic"
[providers.anthropic]
model = "claude-opus-5"
api_key_env = "ANTHROPIC_API_KEY"

# Used when provider or fallback is "openai". Swap base_url/model/key for any
# OpenAI-compatible vendor, e.g. Gemini:
#   base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
#   api_key_env = "GEMINI_API_KEY"
[providers.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-5-mini"             # example; use any model the endpoint serves
api_key_env = "OPENAI_API_KEY"

[tts]
provider = "piper"              # piper | openai
voice = "en_US-lessac-medium"

[learning]
promote_after = 3

[debug]
save_audio = false
```

## 10. Project structure

```
Zade/
  pyproject.toml        # deps: openwakeword, faster-whisper, piper-tts, ollama,
                        #       rapidfuzz, sounddevice, numpy, transformers/torch (Laya),
                        #       anthropic, openai (cloud providers)
  config.example.toml
  docs/
    PRD.md
  zade/
    __main__.py         # main loop: wake → record → stt → route → act → speak
    config.py           # load TOML (stdlib tomllib) into a dict
    audio.py            # mic stream, wake word, VAD recording, chime
    stt.py              # transcribe(): faster-whisper or OpenAI-compatible endpoint
    tts.py              # speak(): piper or OpenAI-compatible endpoint, playback
    router.py           # normalize, shortcut match, Laya, fall through to Qwen
    brain.py            # ask(): picks provider, tool-call loop, fallback, warm-up, VRAM check
    providers.py        # one chat function each for ollama, anthropic, openai-compatible
    actions.py          # allowlist, confirmation gate, shell runner
    memory.py           # SQLite: history, shortcuts, facts, promotion rule
  tests/
    test_router.py      # normalization, fuzzy/prefix matching, thresholds
    test_actions.py     # sudo refusal, confirmation parsing, allowlist dispatch
    test_memory.py      # promotion after 3 uses, declined offers never repeat
  systemd/
    zade.service        # user service: systemctl --user enable --now zade
```

Each module has one job and plain functions. There are no class hierarchies or plugin systems in v1.

## 11. Error handling

| Failure | Behaviour |
|---|---|
| No microphone or audio device lost | Log it, retry every 5 s, and speak "microphone unavailable" once it recovers |
| Ollama not running | Say "My brain is offline", but keep shortcuts and Laya routes working |
| Laya fails to load | Skip step 2 of the router and log a warning |
| Too little VRAM | Run Qwen on the CPU and say nothing unless the answer takes more than 5 s ("thinking…") |
| Shell command times out (30 s) | Kill it and say "that took too long, stopped it" |
| Nothing recognized in the transcript | Say "Sorry, didn't catch that" |
| Database locked or corrupt | Log it and keep running without learning for the session |

## 12. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | **Spikes** | Laya runs on the CPU on this machine and its latency is measured; its interface is documented; `qwen3:4b` tool calling works through Ollama on ROCm; the VRAM sysfs read works |
| M1 | **Voice loop** | The custom "Zade" wake word is trained (openWakeWord training notebook, synthetic Piper samples plus about 20 recordings of your own voice); wake word, recording, STT and TTS echo back what you said |
| M2 | **Actions** | Allowlisted actions work by voice through Qwen tool calling, and the shell confirmation gate works |
| M3 | **Router** | Shortcut match and Laya routing, with NFR-1 met for known actions |
| M4 | **Memory** | History, facts, shortcut promotion (FR-13 to FR-17) |
| M5 | **GPU lifecycle** | Warm-up on wake, idle unload, CPU fallback; NFR-4 and NFR-5 verified with `rocm-smi` |
| M5b | **Cloud providers** | `anthropic` and `openai` LLM providers pass the same tool-call tests as Ollama; cloud STT and TTS work; `llm.fallback` switches correctly when VRAM is busy or the network is down |
| M6 | **Daily driver** | systemd user service, latency logging, a week of personal use with thresholds tuned |

## 13. Risks

| Risk | Mitigation |
|---|---|
| Laya is new and its accuracy is contested in independent tests | The M0 spike tests it on about 50 real commands before committing to it. If it misroutes, lower its role to the confirm band only, or remove step 2 and route to Qwen directly |
| ROCm support for the RDNA4 (gfx1200) card in Ollama | Verify in M0. Ollama's Vulkan backend or the CPU fallback keep Zade working |
| Whisper mishears short commands | Use `initial_prompt` with shortcut phrases and app names to bias recognition; the confirm band catches the rest |
| False wake-word triggers: "Zade" is one short syllable, so it trips more easily than a longer phrase, and names such as "Jade" or "Wade" sound close to it | Train with negative examples of similar words and tune the threshold. If false triggers stay high, switch to "hey Zade" (a config change plus a retrain) |
| A cloud provider is slow, down or rate-limited | 10 s request timeout, then `llm.fallback`; shortcuts and Laya routes never depend on the network |
| API keys leak | Keys only in environment variables, never in `config.toml`, logs or the database |
| Qwen writes a harmful command | The confirmation gate, the sudo ban, the 30 s timeout, and running as a normal user |

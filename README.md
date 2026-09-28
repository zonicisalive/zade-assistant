# Zade

Local-first voice assistant for the Linux desktop. Say "Zade", then a request.

Custom made by Zonic ([@zonicisalive](https://github.com/zonicisalive)) for my own desktop. Anyone is free to
fork it and change it however they wish; see [License](#license).

## Setup

```bash
sudo pacman -S --needed portaudio playerctl wireplumber gtk3 uv   # plus Ollama for the brain
sudo pacman -S --needed ffmpeg wf-recorder libpulse   # the replay buffer ("clip that"): sound and screen
uv python install 3.11 && CC=gcc uv sync   # CC=gcc: evdev builds from source
mkdir -p ~/.local/share/zade/voices ~/.config/zade
uv run python -m piper.download_voices en_US-lessac-medium --data-dir ~/.local/share/zade/voices
mkdir -p ~/.local/share/zade/kokoro && for f in kokoro-v1.0.onnx voices-v1.0.bin; do curl -L -o ~/.local/share/zade/kokoro/$f https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f; done
cp config.example.toml ~/.config/zade/config.toml
```

Pull any Ollama model that supports tool calling and set it as `llm.model` in the config (or in the app).
Zade wakes on "hey jarvis" (or Alexa, Hey Mycroft, Hey Rhasspy) until you use your own wake word. Any
openWakeWord model works: in the app, **Settings → Listening → Wake word → + Add your own**, pick the `.onnx`
file and type the phrase it listens for. A ready "hey Zade" model (trained on ~100k American-accented voices)
is attached to the [latest release](https://github.com/zonicisalive/zade-assistant/releases/latest) as
`hey_zade.onnx`; add it with the phrase `hey zade`. To train one, record yourself with `uv run python -m zade.record_wake`,
then run `scripts/train_wake.py` (I ran it on a free Kaggle GPU).

## My setup

What I used on my own desktop. Nothing here is required; swap in whatever suits your machine.

- Arch Linux with the niri compositor and the Quickshell-based inir shell (Zade's overlay lives inside it)
- AMD Radeon RX 9060 XT 16 GB and a Ryzen 5 7600X; the GPU parts run on Vulkan or ROCm
- Brain: qwen3.5:4b through Ollama (thinking off unless asked), unloaded 30 s after the last request
- Hearing: Qwen3-ASR-0.6B through llama.cpp on the GPU (see below), with the small.en Whisper model on the CPU
  as a fallback
- Wake word: my own "hey Zade", trained on recordings of me and three friends plus synthetic Indian-accented voices
- Voice: Kokoro (af_heart), fully local
- Spotify through Spotify Connect, weather from Open-Meteo, web answers through DuckDuckGo

## Run

- App: open **Zade** from your app launcher (or `qs -p ~/Zade/ui/app.qml`): start/stop, voice and personality, settings, memory and history. Install the launcher entry with `cp ui/zade.desktop ~/.local/share/applications/`.

- Foreground: `uv run python -m zade`
- Service: `cp systemd/zade.service ~/.config/systemd/user/ && systemctl --user enable --now zade`, logs with `journalctl --user -u zade -f`
- Hotkey: Zade writes its PID to `~/.local/share/zade/zade.pid`; `kill -USR1 $(cat ~/.local/share/zade/zade.pid)` makes it listen as if you said the wake word. niri example: `Mod+Z { spawn "sh" "-c" "kill -USR1 $(cat ~/.local/share/zade/zade.pid)"; }`
- Tests: `uv run pytest`

## Cloud providers

Set `llm.provider`, `stt.provider` or `tts.provider` in `~/.config/zade/config.toml`, and put the key in
`~/.config/zade/env` (for example `ANTHROPIC_API_KEY=...`, file mode 600). Everything stays local by default.

## Safety

Shell commands are read back and run only after a spoken "yes". Anything containing sudo, su, pkexec or doas is refused.

## Spotify

"Play Killshot by Eminem" needs a free Spotify developer app:
1. Go to https://developer.spotify.com/dashboard, log in, "Create app" (any name; redirect URI `http://127.0.0.1:8888`).
2. Copy its Client ID and Client secret into `~/.config/zade/env`:
   ```
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   ```
Without keys, Zade opens Spotify's search instead.

## Discord (optional, needs Vencord built from source)

With the ZadeControl plugin in `vencord/zadeControl`, Zade controls Discord directly instead of pressing keys:
"mute me on discord", "deafen", "leave the vc", "join the gaming vc", "call dexorto on discord", "open general in
bitnade on discord", "read the last 3 messages from dexorto", "who messaged me on discord", "who's in the vc", and
"message dexorto on discord saying I'm late", "react fire to dexorto's message", "reply to dexorto on discord saying
on my way", "edit my last message to ...", "delete my last message", "set my discord status to do not disturb".
Messages, replies, edits and deletes always ask for a yes first, naming the person or channel as Discord knows it
(a long message is shown, not read out).

```bash
cp -r ~/Zade/vencord/zadeControl ~/path/to/Vencord/src/userplugins/
cd ~/path/to/Vencord && pnpm build   # then restart Discord and enable ZadeControl in Settings > Vencord > Plugins
```

The plugin listens only on a Unix socket in `$XDG_RUNTIME_DIR/zade` that only you can open, and answers only
requests carrying the secret token it writes to `~/.config/zade/discord-token` (readable only by you). Without
it, Zade falls back to typing into Discord.

## GPU speech recognition (optional, ~0.9 GB VRAM)

The first GPU option I tried: Whisper large-v3-turbo through whisper.cpp's Vulkan backend (works on AMD). It
knows far more names than the CPU model; Zade falls back to the CPU model whenever the server isn't running.

```bash
sudo pacman -S --needed vulkan-headers spirv-headers shaderc
cd ~/.local/share/zade && git clone --depth 1 https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
cmake -B build -DGGML_VULKAN=1 -DGGML_CCACHE=OFF -DCMAKE_BUILD_TYPE=Release && cmake --build build -j --target whisper-server
curl -L -o models/ggml-large-v3-turbo-q8_0.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q8_0.bin
cp ~/Zade/systemd/zade-whisper.service ~/.config/systemd/user/ && systemctl --user daemon-reload
```

Then pick **Settings → Listening → Speech recognition → Large (GPU)** and restart Zade. Zade starts the
service when it starts listening (the model loads in ~0.5 s, while you talk) and stops it after
`stt.keep_alive_s` (30 s) without a request, so it uses no VRAM while idle.

## Qwen3-ASR (optional, ~0.8 GB VRAM while listening)

What I ended up using: Qwen3-ASR-0.6B through llama.cpp's Vulkan backend. On recordings of me and my friends
(Indian accents) it made ~20% fewer word errors than Whisper large-v3-turbo, and answers in ~0.06 s once loaded
(~0.9 s from cold).

```bash
cd ~/.local/share/zade && git clone --depth 1 https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DGGML_VULKAN=ON -DGGML_CCACHE=OFF -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
cmake --build build -j --target llama-server
mkdir -p ../qwen3-asr && cd ../qwen3-asr
for f in Qwen3-ASR-0.6B-Q8_0.gguf mmproj-Qwen3-ASR-0.6B-Q8_0.gguf; do
  curl -L -o $f https://huggingface.co/unslothai/Qwen3-ASR-0.6B-GGUF/resolve/main/$f; done
cp ~/Zade/systemd/zade-qwen-asr.service ~/.config/systemd/user/ && systemctl --user daemon-reload
```

Then pick **Settings → Listening → Speech recognition → Qwen3 (GPU)** and restart Zade.

## Overlay inside your desktop shell (optional, saves ~225 MB)

If you run a Quickshell-based shell (e.g. inir), add this line inside its `ShellRoot { ... }` in `shell.qml`:

```qml
LazyLoader { active: true; source: "file:///home/you/Zade/ui/ZadeHost.qml" }
```

Restart the shell once. From then on, Zade updates to the overlay load by themselves (ZadeHost.qml watches its files). Zade sees the line (setting `ui.host_file`) and no longer starts its own overlay process.
If a shell update removes the line, Zade automatically goes back to running its own.

## License

MIT: use it, fork it, change it and share it as you wish. See [LICENSE](LICENSE).

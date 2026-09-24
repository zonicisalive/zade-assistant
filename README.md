# Zade

Local-first voice assistant for the Linux desktop. Say "Zade", then a request.

## Setup

```bash
sudo pacman -S --needed portaudio playerctl wireplumber gtk3 uv   # plus Ollama with ROCm
ollama pull qwen3:4b-instruct
uv python install 3.11 && CC=gcc uv sync   # CC=gcc: evdev builds from source
mkdir -p ~/.local/share/zade/voices ~/.config/zade
uv run python -m piper.download_voices en_US-lessac-medium --data-dir ~/.local/share/zade/voices
mkdir -p ~/.local/share/zade/kokoro && for f in kokoro-v1.0.onnx voices-v1.0.bin; do curl -L -o ~/.local/share/zade/kokoro/$f https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f; done
cp config.example.toml ~/.config/zade/config.toml
```

Train the "Zade" wake word: see `docs/superpowers/plans/2026-09-23-zade-v1.md`, Task 10. Until then Zade wakes on "hey jarvis".

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

## Overlay inside your desktop shell (optional, saves ~225 MB)

If you run a Quickshell-based shell (e.g. inir), add this line inside its `ShellRoot { ... }` in `shell.qml`:

```qml
LazyLoader { active: true; source: "file:///home/you/Zade/ui/ZadeHost.qml" }
```

Restart the shell once. From then on, Zade updates to the overlay load by themselves (ZadeHost.qml watches its files). Zade sees the line (setting `ui.host_file`) and no longer starts its own overlay process.
If a shell update removes the line, Zade automatically goes back to running its own.

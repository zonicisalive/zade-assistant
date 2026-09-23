# Zade

Local-first voice assistant for the Linux desktop. Say "Zade", then a request.

## Setup

```bash
sudo pacman -S --needed portaudio playerctl wireplumber gtk3 uv   # plus Ollama with ROCm
ollama pull qwen3:4b-instruct
uv python install 3.11 && uv sync
mkdir -p ~/.local/share/zade/voices ~/.config/zade
uv run python -m piper.download_voices en_US-lessac-medium --data-dir ~/.local/share/zade/voices
cp config.example.toml ~/.config/zade/config.toml
```

Train the "Zade" wake word: see `docs/superpowers/plans/2026-09-23-zade-v1.md`, Task 10. Until then Zade wakes on "hey jarvis".

## Run

- Foreground: `uv run zade`
- Service: `cp systemd/zade.service ~/.config/systemd/user/ && systemctl --user enable --now zade`, logs with `journalctl --user -u zade -f`
- Tests: `uv run pytest`

## Cloud providers

Set `llm.provider`, `stt.provider` or `tts.provider` in `~/.config/zade/config.toml`, and put the key in
`~/.config/zade/env` (for example `ANTHROPIC_API_KEY=...`, file mode 600). Everything stays local by default.

## Safety

Shell commands are read back and run only after a spoken "yes". Anything containing sudo, su, pkexec or doas is refused.

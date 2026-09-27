"""Replay buffer: the last seconds of what you hear, your mic and your screen, kept in RAM, saved only on
"clip that".

Two parec recorders (PipeWire) feed ring buffers in memory: the default output's monitor (games, calls,
music) and the default mic; 30 s of both is about 9 MB. The screen is gpu-screen-recorder's own replay mode,
encoded on the GPU and kept in RAM too (tens of MB). Nothing touches the disk until a clip is saved: then
one folder in ~/Videos/Clips gets "mic and sound.mp3", "sound.mp3" and "screen.mp4".
"""

import collections
import datetime
import logging
import pathlib
import shutil
import signal
import subprocess
import tempfile
import threading
import time

import numpy as np

log = logging.getLogger("zade")

RATE = 48000
SOURCES = {"system": ("@DEFAULT_MONITOR@", 2), "mic": ("@DEFAULT_SOURCE@", 1)}  # device, channels
FOLDER = pathlib.Path("~/Videos/Clips").expanduser()


class Ring:
    """Raw audio from one parec, trimmed to the newest `seconds`."""

    def __init__(self, device, channels, seconds):
        self.channels, self.cap = channels, seconds * RATE * channels * 2
        self.chunks, self.size, self.lock = collections.deque(), 0, threading.Lock()
        self.proc = subprocess.Popen(["parec", f"--device={device}", f"--rate={RATE}", f"--channels={channels}",
                                      "--format=s16le", "--raw", "--latency-msec=100"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while chunk := self.proc.stdout.read(RATE // 10 * self.channels * 2):  # 100 ms at a time
            with self.lock:
                self.chunks.append(chunk)
                self.size += len(chunk)
                while self.size - len(self.chunks[0]) >= self.cap:
                    self.size -= len(self.chunks.popleft())

    def last(self, seconds):
        """The newest `seconds` as stereo int16 samples (a mono mic is copied to both sides)."""
        with self.lock:
            raw = b"".join(self.chunks)
        a = np.frombuffer(raw, np.int16).reshape(-1, self.channels)[-seconds * RATE:]
        return np.repeat(a, 2, axis=1) if self.channels == 1 else a

    def stop(self):
        self.proc.terminate()


class Screen:
    """gpu-screen-recorder in replay mode: the last `seconds` of the screen (with both sounds) in RAM; SIGUSR1
    makes it write them to its folder, which is a temporary one: the file is moved into the clip's folder."""

    def __init__(self, seconds):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="zade-screen-"))
        self.proc = subprocess.Popen(
            ["gpu-screen-recorder", "-w", "screen", "-f", "60", "-c", "mp4", "-q", "high", "-k", "auto",
             "-a", "default_output|default_input", "-r", str(seconds), "-replay-storage", "ram", "-o", str(self.dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def save(self, dest, timeout=15):
        if self.proc.poll() is not None:
            raise RuntimeError("the screen recorder stopped: " + (self.proc.stderr.read() or b"").decode()[-200:].strip())
        before = set(self.dir.glob("*"))
        self.proc.send_signal(signal.SIGUSR1)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            new = [p for p in set(self.dir.glob("*.mp4")) - before]
            if new and time.monotonic() - new[0].stat().st_mtime > 0.5:  # finished writing
                shutil.move(new[0], dest)
                return dest
            time.sleep(0.2)
        raise RuntimeError("the screen recorder didn't save in time")

    def stop(self):
        self.proc.terminate()
        shutil.rmtree(self.dir, ignore_errors=True)


def mp3(samples, path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "-",
                    "-codec:a", "libmp3lame", "-q:a", "3", str(path)], input=samples.tobytes(), check=True)


class Replay:
    def __init__(self, seconds=30, sources="both", screen=False):
        self.seconds, self.sources, self.screen_on = seconds, sources, screen
        names = ["system", "mic"] if sources == "both" else [sources]
        self.rings = {n: Ring(*SOURCES[n], seconds) for n in names if n in SOURCES}
        self.screen = None
        if screen:
            if shutil.which("gpu-screen-recorder"):
                self.screen = Screen(seconds)
            else:
                log.warning("screen replay off: gpu-screen-recorder isn't installed")

    def save(self, seconds=None, folder=FOLDER):
        """Save the newest `seconds` (at most what's kept) into a new folder: "mic and sound.mp3" (mixed),
        "sound.mp3" and "screen.mp4", as far as each is recorded. Returns the folder."""
        seconds = min(int(seconds or self.seconds), self.seconds)
        parts = {n: r.last(seconds) for n, r in self.rings.items()}
        n = min((len(p) for p in parts.values()), default=0)
        if n < RATE // 2 and not self.screen:
            raise RuntimeError("There's nothing recorded yet.")
        out = folder / f"{datetime.datetime.now():%Y-%m-%d %H-%M-%S}"
        out.mkdir(parents=True, exist_ok=True)
        if self.screen:  # first: its buffer keeps moving
            try:
                self.screen.save(out / "screen.mp4")
            except RuntimeError as e:
                log.warning("screen replay: %s", e)
        if n >= RATE // 2:
            if len(parts) > 1:
                mix = np.clip(sum(p[-n:].astype(np.int32) for p in parts.values()), -32768, 32767).astype(np.int16)
                mp3(mix, out / "mic and sound.mp3")
            if "system" in parts:
                mp3(parts["system"][-n:], out / "sound.mp3")
            elif "mic" in parts:
                mp3(parts["mic"][-n:], out / "mic.mp3")
        log.info("replay: saved %s", out)
        return out

    def stop(self):
        for r in self.rings.values():
            r.stop()
        if self.screen:
            self.screen.stop()

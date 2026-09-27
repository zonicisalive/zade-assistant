"""Replay buffer: the last seconds of what you hear and your mic, kept in RAM, saved only on "clip that".

Two parec recorders (PipeWire) feed ring buffers in memory: the default output's monitor (games, calls,
music) and the default mic. 30 s of both is about 9 MB. Nothing touches the disk until a clip is saved,
which mixes them and writes one MP3 to ~/Music/Clips.
"""

import collections
import datetime
import logging
import pathlib
import subprocess
import threading

import numpy as np

log = logging.getLogger("zade")

RATE = 48000
SOURCES = {"system": ("@DEFAULT_MONITOR@", 2), "mic": ("@DEFAULT_SOURCE@", 1)}  # device, channels
FOLDER = pathlib.Path("~/Music/Clips").expanduser()


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


class Replay:
    def __init__(self, seconds=30, sources="both"):
        self.seconds, self.sources = seconds, sources
        names = ["system", "mic"] if sources == "both" else [sources]
        self.rings = [Ring(*SOURCES[n], seconds) for n in names if n in SOURCES]

    def save(self, seconds=None, folder=FOLDER):
        """Mix the newest `seconds` (at most what's kept) into an MP3; returns its path."""
        seconds = min(int(seconds or self.seconds), self.seconds)
        parts = [r.last(seconds) for r in self.rings]
        n = min((len(p) for p in parts), default=0)
        if n < RATE // 2:
            raise RuntimeError("There's nothing recorded yet.")
        mix = np.clip(sum(p[-n:].astype(np.int32) for p in parts), -32768, 32767).astype(np.int16)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"clip-{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "-",
                        "-codec:a", "libmp3lame", "-q:a", "3", str(path)], input=mix.tobytes(), check=True)
        log.info("replay: saved %d s to %s", n // RATE, path)
        return path

    def stop(self):
        for r in self.rings:
            r.stop()

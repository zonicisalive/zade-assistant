"""Replay buffer: the last seconds of what you hear, your mic and your screen, kept in RAM, saved only on
"clip that".

Two parec recorders (PipeWire) feed ring buffers in memory: the default output's monitor (games, calls,
music) and the default mic; 30 s of both is about 9 MB. The screen is recorded by wf-recorder on the GPU
into 5-second chunks in /dev/shm (RAM), the oldest overwritten. Nothing touches the disk until a clip is
saved: then one folder in ~/Videos/Clips gets "mic and sound.mp3", "sound.mp3" and "screen.mp4" (picture only).
Zade's service has MemorySwapMax=0, so none of it is ever swapped out to the disk either.
"""

import collections
import datetime
import logging
import pathlib
import shutil
import subprocess
import tempfile
import threading

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


def gpu_device():
    """The render node of the GPU with the most VRAM (the graphics card, not the CPU's built-in one)."""
    def vram(node):
        try:
            return int((node / "device" / "mem_info_vram_total").read_text())
        except (OSError, ValueError):
            return 0
    nodes = sorted(pathlib.Path("/sys/class/drm").glob("renderD*"), key=vram)
    return f"/dev/dri/{nodes[-1].name}" if nodes else None


def screen_output():
    """The monitor to record: the focused one (niri)."""
    import json

    try:
        out = subprocess.run(["niri", "msg", "-j", "focused-output"], capture_output=True, text=True, timeout=3).stdout
        return (json.loads(out) or {}).get("name")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def recorder(hidden):
    """The wf-recorder to run. hidden (the user's clips.hide_from_shell setting, off by default): Zade's own
    copy named zade-screen, which the desktop shell's `pidof wf-recorder` recording indicator doesn't match
    (a symlink still would). The copy is refreshed whenever the system's wf-recorder changes."""
    src = shutil.which("wf-recorder")
    if not src or not hidden:
        return src
    dest = pathlib.Path("~/.local/share/zade/bin/zade-screen").expanduser()
    s = pathlib.Path(src).stat()
    if not dest.exists() or (dest.stat().st_size, int(dest.stat().st_mtime)) != (s.st_size, int(s.st_mtime)):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)  # keeps the mtime, so the check above sees it's current
    return str(dest)


class Screen:
    """The screen, GPU-encoded by wf-recorder (VAAPI) into FFmpeg, which keeps it as 5-second chunks in
    /dev/shm (RAM) and overwrites the oldest: always the last `seconds`, never anything on the disk."""

    CHUNK = 5

    def __init__(self, seconds, binary="wf-recorder"):
        for old in pathlib.Path("/dev/shm").glob("zade-screen-*"):  # left by a Zade that was killed: RAM held for nothing
            shutil.rmtree(old, ignore_errors=True)
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="zade-screen-", dir="/dev/shm"))
        rec = [binary, "-c", "h264_vaapi", "-F", "scale_vaapi=format=nv12", "-r", "60", "-m", "mpegts", "-f", "/dev/stdout"]
        if device := gpu_device():
            rec[1:1] = ["-d", device]
        if output := screen_output():
            rec[1:1] = ["-o", output]
        self.rec = subprocess.Popen(rec, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.seg = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-f", "mpegts", "-i", "-", "-c", "copy", "-f", "segment",
             "-segment_time", str(self.CHUNK), "-segment_wrap", str(seconds // self.CHUNK + 3), "-reset_timestamps", "1",
             str(self.dir / "seg%03d.ts")], stdin=self.rec.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.rec.stdout.close()  # ffmpeg owns the pipe now

    def save(self, dest, seconds):
        """The newest `seconds` of screen into `dest` (MP4): the picture only, the sound is in its own files."""
        if self.seg.poll() is not None or self.rec.poll() is not None:
            raise RuntimeError("the screen recorder stopped")
        chunks = sorted(self.dir.glob("seg*.ts"), key=lambda p: p.stat().st_mtime)
        chunks = chunks[-(seconds // self.CHUNK + 2):]  # a little more, then cut to the last `seconds`
        if not chunks:
            raise RuntimeError("no screen recorded yet")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-sseof", f"-{seconds}", "-i", "concat:" + "|".join(map(str, chunks)),
                        "-map", "0:v", "-c:v", "copy", "-movflags", "+faststart", str(dest)], check=True)
        return dest

    def stop(self):
        for proc in (self.rec, self.seg):
            proc.terminate()
        shutil.rmtree(self.dir, ignore_errors=True)


def mp3(samples, path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "-",
                    "-codec:a", "libmp3lame", "-q:a", "3", str(path)], input=samples.tobytes(), check=True)


class Replay:
    def __init__(self, seconds=30, sources="both", screen=False, hidden=False):
        self.seconds, self.sources, self.screen_on, self.hidden = seconds, sources, screen, hidden
        names = ["system", "mic"] if sources == "both" else [sources]
        self.rings = {n: Ring(*SOURCES[n], seconds) for n in names if n in SOURCES}
        self.screen = None
        if screen:
            if binary := recorder(hidden):
                self.screen = Screen(seconds, binary)
            else:
                log.warning("screen replay off: wf-recorder isn't installed")

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
        mix = None
        if n >= RATE // 2:
            mix = np.clip(sum(p[-n:].astype(np.int32) for p in parts.values()), -32768, 32767).astype(np.int16)
        if self.screen:
            try:
                self.screen.save(out / "screen.mp4", seconds)
            except (RuntimeError, subprocess.CalledProcessError) as e:
                log.warning("screen replay: %s", e)
        if mix is not None:
            if len(parts) > 1:
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

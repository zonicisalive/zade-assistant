"""System status from kernel sensors: CPU/GPU temperature and load, memory, top process. No model needed."""

import pathlib
import subprocess
import time

HWMON = pathlib.Path("/sys/class/hwmon")


def _hwmons(root, name):
    for d in sorted(pathlib.Path(root or HWMON).glob("hwmon*")):
        try:
            if (d / "name").read_text().strip() == name:
                yield d
        except OSError:
            continue


def cpu_temp(root=None):
    for d in _hwmons(root, "k10temp"):  # AMD Ryzen: temp1 is Tctl
        return int((d / "temp1_input").read_text()) / 1000
    for d in _hwmons(root, "coretemp"):  # Intel
        return int((d / "temp1_input").read_text()) / 1000
    return None


def gpu(root=None):
    """(temperature °C, busy %) of the GPU with the most VRAM (skips the CPU's built-in graphics)."""
    best = None
    for d in _hwmons(root, "amdgpu"):
        try:
            vram = int((d / "device" / "mem_info_vram_total").read_text())
            if best is None or vram > best[0]:
                best = (vram, d)
        except OSError:
            continue
    if not best:
        return None
    d = best[1]
    return int((d / "temp1_input").read_text()) / 1000, int((d / "device" / "gpu_busy_percent").read_text())


def memory(text=None):
    info = {}
    for line in (text or pathlib.Path("/proc/meminfo").read_text()).splitlines():
        key, _, value = line.partition(":")
        info[key] = int(value.split()[0]) if value.split() else 0
    total = info["MemTotal"] * 1000 / 1e9
    used = (info["MemTotal"] - info["MemAvailable"]) * 1000 / 1e9
    return round(used, 1), round(total, 1)


def cpu_load():
    def sample():
        fields = [int(x) for x in pathlib.Path("/proc/stat").read_text().split("\n")[0].split()[1:]]
        return sum(fields), fields[3] + fields[4]  # total, idle + iowait

    t1, i1 = sample()
    time.sleep(0.25)
    t2, i2 = sample()
    return round(100 * (1 - (i2 - i1) / max(1, t2 - t1)))


def top_process():
    out = subprocess.run(["ps", "-eo", "comm", "--sort=-%cpu", "--no-headers"],
                         capture_output=True, text=True, timeout=5).stdout.split("\n")
    return out[0].strip() if out and out[0].strip() else "none"


def status(what="all"):
    parts = []
    if what in ("cpu", "all"):
        t = cpu_temp()
        parts.append((f"CPU is at {t:.0f} degrees and " if t is not None else "CPU is ")
                     + f"{cpu_load()}% busy. Top process: {top_process()}.")
    if what in ("gpu", "all"):
        g = gpu()
        parts.append(f"GPU is at {g[0]:.0f} degrees, {g[1]}% busy." if g else "I can't read the GPU sensors.")
    if what in ("ram", "memory", "all"):
        used, total = memory()
        parts.append(f"{used} of {total} gigabytes of memory in use.")
    return " ".join(parts) or f"I can't report {what}."

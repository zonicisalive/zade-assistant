from zade import system


def hwmon(root, n, name, temp, vram=None, busy=None):
    d = root / f"hwmon{n}"
    (d / "device").mkdir(parents=True)
    (d / "name").write_text(name + "\n")
    (d / "temp1_input").write_text(str(temp))
    if vram is not None:
        (d / "device" / "mem_info_vram_total").write_text(str(vram))
        (d / "device" / "gpu_busy_percent").write_text(str(busy))


def test_temperatures_pick_the_real_gpu(tmp_path):
    hwmon(tmp_path, 2, "amdgpu", 47000, vram=16 * 2**30, busy=24)
    hwmon(tmp_path, 3, "amdgpu", 54000, vram=512 * 2**20, busy=0)  # the CPU's built-in graphics
    hwmon(tmp_path, 4, "k10temp", 75500)
    assert system.cpu_temp(tmp_path) == 75.5
    assert system.gpu(tmp_path) == (47.0, 24)


def test_memory_from_meminfo():
    text = "MemTotal:       32000000 kB\nMemFree:  1000 kB\nMemAvailable:   22000000 kB\n"
    assert system.memory(text) == (10.0, 32.0)  # used GB, total GB (decimal, like most tools show)


def test_status_sentence(monkeypatch):
    monkeypatch.setattr(system, "cpu_temp", lambda root=None: 75.5)
    monkeypatch.setattr(system, "gpu", lambda root=None: (47.0, 24))
    monkeypatch.setattr(system, "memory", lambda text=None: (10.0, 32.0))
    monkeypatch.setattr(system, "cpu_load", lambda: 12)
    monkeypatch.setattr(system, "top_process", lambda: "firefox")
    assert system.status("gpu") == "GPU is at 47 degrees, 24% busy."
    assert system.status("cpu") == "CPU is at 76 degrees and 12% busy. Top process: firefox."
    assert system.status("ram") == "10.0 of 32.0 gigabytes of memory in use."
    assert system.status("all").startswith("CPU is at 76 degrees")

"""M0 spike: Laya accuracy and latency on real phrases. Usage: uv run python scripts/spike_laya.py [file]"""
import pathlib
import statistics
import sys
import time

import laya

from zade import router

agent = laya.load("convaiinnovations/laya")
path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tests/data/commands.txt")
rows = [[s.strip() for s in line.split("|")] for line in path.read_text().splitlines() if line.strip()]
table = {"dev": [{"name": "shell", "args": {"cmd": "true"}}]}
hits, times = 0, []
for text, want in rows:
    t = time.perf_counter()
    _, label, conf = router.laya_pick(agent.predict, router.normalize(text), table)
    times.append(time.perf_counter() - t)
    hits += label == want
    print(f"{'OK ' if label == want else 'BAD'} {conf:.2f} {text!r} -> {label!r} (want {want!r})")
print(f"accuracy {hits}/{len(rows)}  p50 {statistics.median(times) * 1000:.0f} ms  max {max(times) * 1000:.0f} ms")

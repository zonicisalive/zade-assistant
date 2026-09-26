"""Hold-to-talk: holding a key (Win by default) alone for a moment acts like saying the wake word.

Reads keyboards through evdev (/dev/input, needs the `input` group). Only the hold key's state and
"some other key went down" are used; no key is ever recorded or logged.
"""

import logging
import pathlib
import select
import threading
import time

log = logging.getLogger("zade")


class HoldDetector:
    def __init__(self, hold_code, hold_s):
        self.hold_code, self.hold_s = hold_code, hold_s
        self.since = None  # when the hold key went down, None when not held
        self.clean = False  # no other key pressed during this hold
        self.fired = False

    def key(self, code, value, t):
        """value: 1 = down, 0 = up, 2 = key repeat (evdev convention)."""
        if code == self.hold_code:
            if value == 1:
                self.since, self.clean, self.fired = t, True, False
            elif value == 0:
                self.since = None
        elif value == 1:
            self.clean = False  # a combo like Win+Z, not a hold

    def held(self):
        return self.since is not None

    def cancelled(self):
        """Another key went down during this hold: it was a shortcut, not a request."""
        return not self.clean

    def due(self, t):
        if self.since is not None and self.clean and not self.fired and t - self.since >= self.hold_s:
            self.fired = True
            return True
        return False


def keyboard_paths(byid=pathlib.Path("/dev/input/by-id")):
    """Keyboards from udev's *-event-kbd links: instant, instead of opening all ~30 input devices."""
    try:
        return sorted({str(link.resolve()) for link in byid.glob("*-event-kbd")})
    except OSError:
        return []


def _keyboards(ecodes, evdev, hold_code):
    out = []
    for path in keyboard_paths() or evdev.list_devices():  # full scan only if udev links are missing
        try:
            dev = evdev.InputDevice(path)
            if hold_code in dev.capabilities().get(ecodes.EV_KEY, []):
                out.append(dev)
            else:
                dev.close()
        except OSError:
            continue
    return out


def watch_all(bindings):
    """One background thread for all hold keys. bindings: [(key name, hold seconds, threading.Event)].
    Returns a HoldDetector per binding (its held()/cancelled() drive push-to-talk), or None each if unavailable."""
    try:
        import evdev
        from evdev import ecodes
    except ImportError as e:
        log.warning("hotkeys disabled, evdev missing: %s", e)
        return [None] * len(bindings)
    codes = [ecodes.ecodes[key] for key, _, _ in bindings]
    devices = _keyboards(ecodes, evdev, codes[0]) if codes else []
    if not devices:
        log.warning("hotkeys disabled: no readable keyboard (is the user in the 'input' group?)")
        return [None] * len(bindings)
    detectors = [HoldDetector(code, hold_s) for code, (_, hold_s, _) in zip(codes, bindings)]

    def rescan():
        """Keyboards plugged in (or back) since: USB replug, resume from suspend, a Bluetooth reconnect."""
        known = {d.path for d in devices}
        for dev in _keyboards(ecodes, evdev, codes[0]):
            if dev.path in known:
                dev.close()
            else:
                devices.append(dev)
                log.info("hotkeys: keyboard added (%s)", dev.name)

    def loop():
        last_scan = time.monotonic()
        while True:
            ready, _, _ = select.select(devices, [], [], 0.05)
            now = time.monotonic()
            for dev in ready:
                try:
                    for ev in dev.read():
                        if ev.type == ecodes.EV_KEY:
                            for d in detectors:
                                d.key(ev.code, ev.value, now)
                except OSError:  # keyboard unplugged: forget it, and any key it was holding
                    devices.remove(dev)
                    dev.close()
                    for d in detectors:
                        d.since = None
            if now - last_scan > 3:
                last_scan = now
                rescan()
            for d, (key, hold_s, trigger) in zip(detectors, bindings):
                if d.due(time.monotonic()):
                    log.info("hotkey: %s held %.1fs", key, hold_s)
                    trigger.set()

    threading.Thread(target=loop, daemon=True, name="hotkeys").start()
    log.info("hotkeys: %s (%d keyboards)", ", ".join(f"hold {k} {h:.1f}s" for k, h, _ in bindings), len(devices))
    return detectors

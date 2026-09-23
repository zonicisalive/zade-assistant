"""Hold-to-talk: holding a key (Win by default) alone for a moment acts like saying the wake word.

Reads keyboards through evdev (/dev/input, needs the `input` group). Only the hold key's state and
"some other key went down" are used; no key is ever recorded or logged.
"""

import logging
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


def _keyboards(ecodes, evdev, hold_code):
    out = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            if hold_code in dev.capabilities().get(ecodes.EV_KEY, []):
                out.append(dev)
            else:
                dev.close()
        except OSError:
            continue
    return out


def watch(cfg, trigger):
    """Background thread: set `trigger` whenever the hold key is held alone long enough.
    Returns the HoldDetector (its held() tells when the key is released), or None if unavailable."""
    try:
        import evdev
        from evdev import ecodes
    except ImportError as e:
        log.warning("hold-to-talk disabled, evdev missing: %s", e)
        return None
    h = cfg["hotkey"]
    code = ecodes.ecodes[h["key"]]
    devices = _keyboards(ecodes, evdev, code)
    if not devices:
        log.warning("hold-to-talk disabled: no readable keyboard (is the user in the 'input' group?)")
        return None

    detector = HoldDetector(code, h["hold_s"])

    def loop():
        while True:
            ready, _, _ = select.select(devices, [], [], 0.05)
            for dev in ready:
                try:
                    for ev in dev.read():
                        if ev.type == ecodes.EV_KEY:
                            detector.key(ev.code, ev.value, time.monotonic())
                except OSError:  # keyboard unplugged
                    devices.remove(dev)
            if detector.due(time.monotonic()):
                log.info("hold-to-talk: %s held %.1fs", h["key"], h["hold_s"])
                trigger.set()

    threading.Thread(target=loop, daemon=True, name="hotkey").start()
    log.info("hold-to-talk: hold %s for %.1fs (%d keyboards)", h["key"], h["hold_s"], len(devices))
    return detector

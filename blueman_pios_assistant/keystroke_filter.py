"""Keystroke debouncer daemon.

Grabs matching BT keyboards, forwards events through a uinput virtual keyboard,
and drops same-key press events that arrive faster than a human could type.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Iterable

try:
    import evdev
    from evdev import UInput, categorize, ecodes
except ImportError:  # pragma: no cover - runtime-only dep
    print("python-evdev is required. Install with: sudo apt install python3-evdev",
          file=sys.stderr)
    raise

from .config import Config

log = logging.getLogger("blueman-pios-assistant.debouncer")


def _looks_like_bt_keyboard(dev: "evdev.InputDevice", filters: tuple[str, ...]) -> bool:
    caps = dev.capabilities()
    if ecodes.EV_KEY not in caps:
        return False
    keys = set(caps[ecodes.EV_KEY])
    # A real keyboard reports at least the alphabetic key range.
    if ecodes.KEY_A not in keys or ecodes.KEY_Z not in keys:
        return False
    # Blueman/BlueZ input devices live under the bluetooth bus.
    phys = (dev.phys or "").lower()
    uniq = (dev.uniq or "").lower()
    on_bt_bus = "bluetooth" in phys or ":" in uniq  # BT MACs contain colons
    if not on_bt_bus:
        return False
    if not filters:
        return True
    name = (dev.name or "").lower()
    return any(f.lower() in name for f in filters)


def find_targets(filters: tuple[str, ...]) -> list["evdev.InputDevice"]:
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    return [d for d in devices if _looks_like_bt_keyboard(d, filters)]


class Debouncer:
    def __init__(self, device: "evdev.InputDevice", min_interval_ms: int) -> None:
        self.device = device
        self.min_interval_s = min_interval_ms / 1000.0
        self._last_press: dict[int, float] = {}
        caps = device.capabilities(verbose=False)
        # uinput only takes EV_KEY/EV_SYN for a plain keyboard; strip the rest.
        forward_caps = {ecodes.EV_KEY: caps.get(ecodes.EV_KEY, [])}
        self.ui = UInput(
            forward_caps,
            name=f"{device.name} (debounced)",
            vendor=device.info.vendor,
            product=device.info.product,
        )

    def run(self) -> None:
        self.device.grab()
        log.info("debouncing %s (%s) @ %.0f ms",
                 self.device.name, self.device.path, self.min_interval_s * 1000)
        try:
            for event in self.device.read_loop():
                if event.type == ecodes.EV_KEY and event.value == 1:  # key press
                    now = time.monotonic()
                    last = self._last_press.get(event.code, 0.0)
                    if now - last < self.min_interval_s:
                        log.debug("dropped repeat of code=%d dt=%.1fms",
                                  event.code, (now - last) * 1000)
                        continue
                    self._last_press[event.code] = now
                self.ui.write_event(event)
                self.ui.syn()
        finally:
            try:
                self.device.ungrab()
            except OSError:
                pass
            self.ui.close()


def _run_forever(cfg: Config) -> int:
    if not cfg.keystroke_enabled:
        log.info("keystroke filter disabled in config; exiting")
        return 0
    targets = find_targets(cfg.device_name_filter)
    if not targets:
        log.warning("no matching BT keyboard found; will retry on reconnect")
        return 2
    # For a first release we only manage the first match; more than one is rare.
    debouncer = Debouncer(targets[0], cfg.min_repeat_interval_ms)
    debouncer.run()
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to config.ini (overrides default location)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.load(args.config) if args.config else Config.load()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    return _run_forever(cfg)


if __name__ == "__main__":
    sys.exit(main())

"""Keystroke debouncer daemon.

Grabs matching BT keyboards, forwards events through a uinput virtual keyboard,
and applies three filters that address BT keyboard misbehavior:

1. Bounce filter: same-key presses (EV_KEY value=1) arriving faster than
   ``min_repeat_interval_ms`` are dropped, so a flaky BT link that emits
   duplicate presses can't produce "thiiiiiis" from a single tap.

2. Stuck-key cutoff: kernel autorepeat (EV_KEY value=2) is capped at
   ``max_repeat_hold_ms`` per press. Beyond that, we synthesize a release
   and drop further repeats for that key until we see a real release. This
   is the fix for the classic BT symptom where the release packet is lost
   and the kernel keeps autorepeating forever ("aaaaaaaa" until you tap
   another key).

3. Missed-release recovery: if a fresh press arrives for a key we still
   think is held (kernel got confused), we emit a synthetic release first,
   then let the new press through — so the key never stays stuck.

Sluggishness fix: the previous version called ``syn()`` after every event
including the kernel's own SYN_REPORT frames, doubling the syn traffic on
every keystroke. We now forward SYN frames as-is and only syn manually
after events we synthesize ourselves.
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
    from evdev import InputEvent, UInput, ecodes
except ImportError:  # pragma: no cover - runtime-only dep
    print("python-evdev is required. Install with: sudo apt install python3-evdev",
          file=sys.stderr)
    raise

from blueman_pios_assistant.config import Config

log = logging.getLogger("blueman-pios-assistant.debouncer")


def _looks_like_bt_keyboard(dev: "evdev.InputDevice", filters: tuple[str, ...]) -> bool:
    caps = dev.capabilities()
    if ecodes.EV_KEY not in caps:
        return False
    keys = set(caps[ecodes.EV_KEY])
    if ecodes.KEY_A not in keys or ecodes.KEY_Z not in keys:
        return False
    # Only real BT devices. BUS_BLUETOOTH == 5; BUS_VIRTUAL (our own uinput
    # clone) is 6, and we must never grab our own output.
    if dev.info.bustype != 0x05:
        return False
    if not filters:
        return True
    name = (dev.name or "").lower()
    return any(f.lower() in name for f in filters)


def find_targets(filters: tuple[str, ...]) -> list["evdev.InputDevice"]:
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    return [d for d in devices if _looks_like_bt_keyboard(d, filters)]


class _KeyState:
    __slots__ = ("pressed", "press_ts", "last_forward_press_ts")

    def __init__(self) -> None:
        self.pressed = False
        self.press_ts = 0.0
        self.last_forward_press_ts = 0.0


class Debouncer:
    def __init__(
        self,
        device: "evdev.InputDevice",
        min_interval_ms: int,
        max_repeat_hold_ms: int,
    ) -> None:
        self.device = device
        self.min_interval_s = min_interval_ms / 1000.0
        self.max_repeat_hold_s = max_repeat_hold_ms / 1000.0
        self._state: dict[int, _KeyState] = {}

        caps = device.capabilities(verbose=False)
        forward_caps = {ecodes.EV_KEY: caps.get(ecodes.EV_KEY, [])}
        self.ui = UInput(
            forward_caps,
            name=f"{device.name} (debounced)",
            vendor=device.info.vendor,
            product=device.info.product,
        )

    def _state_for(self, code: int) -> _KeyState:
        st = self._state.get(code)
        if st is None:
            st = _KeyState()
            self._state[code] = st
        return st

    def _emit_release(self, code: int) -> None:
        """Synthesize a release for a key we believe is stuck."""
        ev = InputEvent(0, 0, ecodes.EV_KEY, code, 0)
        self.ui.write_event(ev)
        self.ui.syn()

    def run(self) -> None:
        self.device.grab()
        log.info(
            "debouncing %s (%s): bounce<%.0fms, hold cap %.0fms",
            self.device.name,
            self.device.path,
            self.min_interval_s * 1000,
            self.max_repeat_hold_s * 1000,
        )
        try:
            for event in self.device.read_loop():
                if event.type == ecodes.EV_KEY:
                    if not self._handle_key(event):
                        continue
                self.ui.write_event(event)
        finally:
            try:
                self.device.ungrab()
            except OSError:
                pass
            self.ui.close()

    def _handle_key(self, event: "InputEvent") -> bool:
        """Return True to forward the event, False to drop it."""
        code = event.code
        st = self._state_for(code)
        now = time.monotonic()

        if event.value == 1:  # press
            # Missed-release recovery: kernel/BT skipped a release, so this
            # press looks like the second half of a stuck key. Cut the old
            # press first, then let the new one through if it clears bounce.
            if st.pressed:
                self._emit_release(code)
                st.pressed = False

            if now - st.last_forward_press_ts < self.min_interval_s:
                return False  # bounce
            st.last_forward_press_ts = now
            st.press_ts = now
            st.pressed = True
            return True

        if event.value == 2:  # autorepeat
            if not st.pressed:
                # We already force-released; kernel is still repeating because
                # the physical release never arrived. Swallow until value=0.
                return False
            if now - st.press_ts > self.max_repeat_hold_s:
                # Sticky-key cutoff: kernel has been repeating for longer than
                # any human keeps a letter held. Assume the release packet was
                # lost and cut the stream.
                self._emit_release(code)
                st.pressed = False
                return False
            return True

        if event.value == 0:  # release
            st.pressed = False
            return True

        return True


def _run_forever(cfg: Config) -> int:
    if not cfg.keystroke_enabled:
        log.info("keystroke filter disabled in config; exiting")
        return 0
    targets = find_targets(cfg.device_name_filter)
    if not targets:
        log.warning("no matching BT keyboard found; will retry on reconnect")
        return 2
    debouncer = Debouncer(
        targets[0],
        cfg.min_repeat_interval_ms,
        cfg.max_repeat_hold_ms,
    )
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

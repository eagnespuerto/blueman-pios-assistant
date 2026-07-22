"""Blueman applet plugin entry point.

Blueman discovers plugins by scanning its applet plugin directory for classes
that subclass ``AppletPlugin``. Drop this file into
``/usr/lib/python3/dist-packages/blueman/plugins/applet/`` (install.sh does this)
and enable it from Blueman's plugin manager.
"""

from __future__ import annotations

import logging
import subprocess
from gettext import gettext as _

try:
    from blueman.plugins.AppletPlugin import AppletPlugin
except ImportError:  # pragma: no cover - only importable inside blueman
    AppletPlugin = object  # type: ignore

from .config import Config
from .signal_control import apply_tx_power

log = logging.getLogger("blueman-pios-assistant.plugin")


class PiOSAssistant(AppletPlugin):  # type: ignore[misc]
    __author__ = "eagnespuerto"
    __description__ = _(
        "PiOS Bluetooth assistant: reduces adapter TX power and debounces BT "
        "keyboard repeats so you stop typing 'thiiiiiis'."
    )
    __icon__ = "bluetooth-symbolic"

    def on_load(self) -> None:
        self._cfg = Config.load()
        log.info("PiOSAssistant loaded: tx=%d dBm, debounce=%s @ %d ms",
                 self._cfg.tx_power_dbm,
                 self._cfg.keystroke_enabled,
                 self._cfg.min_repeat_interval_ms)
        apply_tx_power(self._cfg.tx_power_dbm)
        self._ensure_debouncer_running()

    def on_unload(self) -> None:
        log.info("PiOSAssistant unloaded")

    def on_device_connected(self, device) -> None:  # noqa: ANN001 - blueman signature
        # Re-apply TX power each time a device joins; some adapters reset on connect.
        apply_tx_power(self._cfg.tx_power_dbm)
        self._ensure_debouncer_running()

    def _ensure_debouncer_running(self) -> None:
        if not self._cfg.keystroke_enabled:
            return
        try:
            subprocess.run(
                ["systemctl", "--user", "start", "blueman-pios-assistant.service"],
                check=False, timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("could not start debouncer service: %s", e)

"""Config file loader shared between the applet plugin and the debouncer daemon."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("BLUEMAN_PIOS_CONFIG")
    or (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "blueman-pios-assistant" / "config.ini")
)

DEFAULTS = {
    "signal": {
        "tx_power_dbm": "4",
    },
    "keystroke": {
        "enabled": "true",
        "min_repeat_interval_ms": "40",
        "max_repeat_hold_ms": "800",
        "device_name_filter": "",
    },
}


@dataclass
class Config:
    tx_power_dbm: int
    keystroke_enabled: bool
    min_repeat_interval_ms: int
    max_repeat_hold_ms: int
    device_name_filter: tuple[str, ...]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        parser = configparser.ConfigParser()
        parser.read_dict(DEFAULTS)
        if path.exists():
            parser.read(path)

        filters_raw = parser.get("keystroke", "device_name_filter", fallback="")
        filters = tuple(f.strip() for f in filters_raw.split(",") if f.strip())

        return cls(
            tx_power_dbm=parser.getint("signal", "tx_power_dbm"),
            keystroke_enabled=parser.getboolean("keystroke", "enabled"),
            min_repeat_interval_ms=parser.getint("keystroke", "min_repeat_interval_ms"),
            max_repeat_hold_ms=parser.getint("keystroke", "max_repeat_hold_ms"),
            device_name_filter=filters,
        )

    def save(self, path: Path = CONFIG_PATH) -> None:
        parser = configparser.ConfigParser()
        parser["signal"] = {"tx_power_dbm": str(self.tx_power_dbm)}
        parser["keystroke"] = {
            "enabled": "true" if self.keystroke_enabled else "false",
            "min_repeat_interval_ms": str(self.min_repeat_interval_ms),
            "max_repeat_hold_ms": str(self.max_repeat_hold_ms),
            "device_name_filter": ",".join(self.device_name_filter),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            parser.write(f)

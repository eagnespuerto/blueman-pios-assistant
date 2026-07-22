# blueman-pios-assistant

A [Blueman](https://github.com/blueman-project/blueman) applet plugin for Raspberry Pi OS that fixes two everyday Bluetooth annoyances:

1. **Signal strength tamer** — reduces the adapter's TX power so a nearby device isn't blasting the airwaves (useful when you keep dropping other 2.4 GHz traffic, or a keyboard sits 20 cm from the Pi).
2. **Keystroke debouncer** — filters same-key repeats that arrive faster than a human could type, so `thiiiiiiiiiiis` becomes `this`. Uses `python-evdev` + `uinput` to intercept the paired BT keyboard and re-emit clean events.

Runs as a Blueman applet plugin (adds a "PiOS Assistant" entry in the tray menu and a preferences page) and ships a systemd user service that keeps the debouncer alive across reconnects.

## Requirements

- Raspberry Pi OS Bookworm (or any Debian-based distro with BlueZ 5.66+)
- `blueman >= 2.3`
- Python 3.9+
- `python3-evdev`, `python3-gi`, `bluez-tools` (for `btmgmt`)

```sh
sudo apt install blueman bluez-tools python3-evdev python3-gi
```

## Install

```sh
git clone https://github.com/eagnespuerto/blueman-pios-assistant.git
cd blueman-pios-assistant
sudo ./install.sh
```

`install.sh` copies the plugin into Blueman's applet plugin directory, drops the systemd user unit, and adds a udev rule so the debouncer can open `/dev/uinput` without root.

Log out and back in, then right-click the Blueman tray icon → **Plugins** → enable **PiOSAssistant**.

### Plugin doesn't appear in the panel?

Blueman scans its applet plugin directory once at applet startup, and it silently skips plugins that raise on import. Two things to check:

1. Restart the applet after install so it re-scans:
   ```sh
   pkill -f blueman-applet; nohup blueman-applet >/dev/null 2>&1 &
   ```
2. Look for import errors:
   ```sh
   blueman-applet --loglevel debug 2>&1 | grep -i pios
   ```
   Typical culprits are a missing `python3-evdev` or a Blueman version older than 2.3 (which uses a different plugin base class).

## Configuration

Preferences live in `~/.config/blueman-pios-assistant/config.ini`:

```ini
[signal]
# TX power in dBm. BlueZ range is typically -20 to +20.
# Lower = weaker signal. Default keeps a small margin below max.
tx_power_dbm = 4

[keystroke]
enabled = true
# Minimum ms between same-key presses. 40 ms ≈ 25 chars/sec,
# faster than any human sustains. Raise for stubborn keyboards.
min_repeat_interval_ms = 40
# Maximum ms the kernel is allowed to autorepeat a single key before we
# assume the release packet was lost and force a synthetic release.
# 800 ms is well past any deliberate hold in normal typing; raise it if
# you actually want to hold keys (gaming, arrow-key scrolling).
max_repeat_hold_ms = 800
# Comma-separated substrings matched against the evdev device name.
# Leave blank to filter every keyboard reported over BT.
device_name_filter =
```

The applet preferences dialog writes the same file, so hand-editing and the GUI stay in sync.

## How the debouncer works

`keystroke_filter.py` opens the BT keyboard as an `evdev.InputDevice`, grabs it exclusively, and re-emits events through a `uinput` virtual keyboard. It applies three filters:

- **Bounce filter**: same-key press events (`value == 1`) arriving within `min_repeat_interval_ms` of the previous forwarded press are dropped. Kills "thiiiiiis" from a flaky BT link that duplicates single taps.
- **Stuck-key cutoff**: kernel autorepeat (`value == 2`) is capped at `max_repeat_hold_ms` per press. Past that, the daemon synthesizes a release and drops further repeats until it sees the real release. This is the fix for sticky keys — the classic case where the BT release packet is lost and the kernel autorepeats forever until you tap a different key.
- **Missed-release recovery**: if a fresh press arrives for a key the daemon still tracks as held, it emits a synthetic release before letting the new press through, so the key can never end up permanently latched.

SYN frames from the kernel are forwarded verbatim; the daemon only calls `syn()` after events it synthesizes itself. Earlier versions double-syn'd every event, which showed up as a slight typing lag.

## How the signal tamer works

On plugin load and on every device connect, `signal_control.py` calls:

```sh
btmgmt --index <adapter> power off
btmgmt --index <adapter> phy BR1M2 BR1M3 LE1M
btmgmt --index <adapter> power on
hcitool cmd 0x03 0x0018 <tx_power_hex>   # HCI_Write_Class_Of_Device fallback
```

BlueZ doesn't expose a single portable "TX power" knob, so the plugin uses the management API when available and falls back to per-connection HCI commands for older kernels. The exact TX power step depends on the Pi's controller; the plugin logs what actually took effect.

## Uninstall

```sh
sudo ./install.sh --uninstall
```

## License

MIT

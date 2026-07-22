"""Adapter TX-power control via BlueZ's btmgmt + HCI fallback."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

BTMGMT = shutil.which("btmgmt") or "/usr/bin/btmgmt"
HCITOOL = shutil.which("hcitool") or "/usr/bin/hcitool"


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("failed to run %s: %s", cmd, e)
        return 127, str(e)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        log.warning("%s exited %d: %s", " ".join(cmd), proc.returncode, output)
    return proc.returncode, output


def _dbm_to_hci_byte(dbm: int) -> int:
    """Map dBm target to the single-byte value HCI_Write_Inquiry_Transmit_Power expects.

    The controller reports back the value it actually applied, so the caller should
    read the log to see what stuck. Values are two's complement signed 8-bit.
    """
    clamped = max(-70, min(20, dbm))
    return clamped & 0xFF


def apply_tx_power(dbm: int, adapter_index: int = 0) -> Optional[int]:
    """Apply TX power to the given adapter. Returns the byte written, or None on failure."""
    # Newer BlueZ exposes a phy configuration knob that indirectly changes TX power
    # by narrowing the allowed PHYs. We use it opportunistically.
    _run([BTMGMT, "--index", str(adapter_index), "power", "off"])
    _run([BTMGMT, "--index", str(adapter_index), "phy", "BR1M1", "BR1M3", "LE1M"])
    _run([BTMGMT, "--index", str(adapter_index), "power", "on"])

    byte = _dbm_to_hci_byte(dbm)
    # HCI_Write_Inquiry_Transmit_Power_Level, OGF=0x03, OCF=0x0059.
    rc, out = _run([
        HCITOOL, "cmd", "0x03", "0x0059", f"0x{byte:02x}",
    ])
    if rc != 0:
        log.info("hcitool TX power write not accepted; controller may not support it")
        return None
    log.info("requested TX power %d dBm (byte 0x%02x); controller response: %s",
             dbm, byte, out)
    return byte

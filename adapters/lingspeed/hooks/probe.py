#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lingspeed adapter probe (04 §3 interface contract): device discovery.

Returns:
  {available: bool, devices: [...], toolchain_ok: bool, versions: {...}}

Phase 1 paper smoke test: with no Lingspeed hardware on this machine available=false
but toolchain_ok=template, and the platform still runs all 8 stages via the generic
fallback.
After live deployment (GPU node with /dev/lscd present) this file auto-switches to live.
"""
import json
import os
import sys

from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent.parent
VENDORKB = os.path.join(ADAPTER_DIR, 'vendorkb')

# real Lingspeed toolchain (verified 2026-11): the /opt/luca symlink is dangling, must use the real path
LUCA_REAL = os.environ.get('LUCA_PATH') or '/opt/luca-3.8.0'
LSCC = os.path.join(LUCA_REAL, 'lsgpu_llvm', 'bin', 'lscc')
DEV_NODE = '/dev/lscd'  # crw------- 235,0, no mknod needed


def probe() -> dict:
    has_toolchain = os.path.exists(LSCC) and os.path.exists(LUCA_REAL)
    hw_available = has_toolchain and os.path.exists(DEV_NODE)

    known_chips = []
    if os.path.exists(VENDORKB):
        for chip in ('lsc1000',):
            if os.path.exists(os.path.join(VENDORKB, chip + '.json')):
                known_chips.append(chip)

    return {
        "available": hw_available,
        "devices": known_chips or ["lsc1000"],
        "toolchain_ok": has_toolchain,
        "paper_mode": not hw_available,
        "versions": {
            "luca": os.path.basename(LUCA_REAL) if has_toolchain else 'unknown',
            "adapter": "1.0.0",
            "environment": "paper-template" if not hw_available else "live"
        },
        "note": ("no Lingspeed toolchain/device detected on this machine -> "
                 "platform runs all 8 stages via generic fallback "
                 "(Phase 1 paper smoke); real environment (/dev/lscd + /opt/luca-3.8.0) "
                 "auto-switches to live" if not hw_available else
                 f"Lingspeed device ready: {DEV_NODE}, lscc={LSCC}")
    }


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))

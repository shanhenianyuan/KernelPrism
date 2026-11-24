#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metax adapter probe (04 §3 interface contract): device discovery.

Returns:
  {available: bool, devices: [...], toolchain_ok: bool, versions: {...}}

Phase 1 paper smoke test: with no Metax hardware on this machine available=false
but toolchain_ok=template, and the platform still runs all 8 stages via the generic
fallback.
After live deployment (/opt/maca + /dev/mxc0) this file auto-switches to live.
"""
import json
import os

from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent.parent
VENDORKB = os.path.join(ADAPTER_DIR, 'vendorkb')

# real Metax toolchain (verified by comparison): /opt/maca/bin/mxcc (same slot as lscc)
MACA_REAL = os.environ.get('MACA_PATH') or '/opt/maca'
MXCC = os.path.join(MACA_REAL, 'bin', 'mxcc')
DEV_NODES = ['/dev/mxc0', '/dev/metax_gpu', '/dev/mxc']  # awaiting live verification


def probe() -> dict:
    has_toolchain = os.path.exists(MXCC) and os.path.exists(MACA_REAL)
    hw_available = has_toolchain and any(os.path.exists(d) for d in DEV_NODES)

    known_chips = []
    if os.path.exists(VENDORKB):
        for chip in ('c550',):
            if os.path.exists(os.path.join(VENDORKB, chip + '.json')):
                known_chips.append(chip)

    return {
        "available": hw_available,
        "devices": known_chips or ["c550"],
        "toolchain_ok": has_toolchain,
        "paper_mode": not hw_available,
        "versions": {
            "mxmaca": os.path.basename(MACA_REAL) if has_toolchain else 'unknown',
            "adapter": "1.0.0",
            "environment": "paper-template" if not hw_available else "live"
        },
        "note": ("no Metax toolchain/device detected on this machine -> "
                 "platform runs all 8 stages via generic fallback "
                 "(Phase 1 paper smoke); real environment (/dev/mxc0 + /opt/maca/bin/mxcc) "
                 "auto-switches to live" if not hw_available else
                 f"Metax device ready: {','.join(DEV_NODES)}, mxcc={MXCC}")
    }


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascend adapter probe (04 §3 interface contract): device discovery.

Returns:
  {available: bool, devices: [...], toolchain_ok: bool, versions: {...}}

Phase 1 paper smoke test: with no Ascend hardware on this machine available=false
but toolchain_ok=template, and the platform still runs all 8 stages via the generic
fallback (06-roadmap Phase 1 risk mitigation: pure CPU sandbox smoke).
After live deployment this file is genuinely superseded by environment probing.
"""
import json
import os
import sys

from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent.parent
VENDORKB = os.path.join(ADAPTER_DIR, 'vendorkb')


def probe() -> dict:
    # real hardware probe point (conventional Ascend env var names)
    ascend_home = os.environ.get('ASCEND_HOME') or os.environ.get('ASCEND_TOOLKIT_HOME')
    npu_visible = os.environ.get('ASCEND_RT_VISIBLE_DEVICES')

    has_toolchain = bool(ascend_home) or bool(
        os.environ.get('CANN_INSTALL_PATH'))

    hw_available = has_toolchain and bool(npu_visible)
    known_chips = []
    if hw_available:
        for chip in ('asc910', 'asc950'):
            if os.path.exists(os.path.join(VENDORKB, chip + '.json')):
                known_chips.append(chip)

    return {
        "available": hw_available,
        "devices": known_chips or ["asc910"],
        "toolchain_ok": hw_available,
        "paper_mode": not hw_available,
        "versions": {
            "cann": os.environ.get('CANN_VERSION', 'unknown'),
            "adapter": "2.4.0",
            "environment": "paper-template" if not hw_available else "live"
        },
        "note": ("no Ascend toolchain/device detected on this machine -> "
                 "platform runs all 8 stages via generic fallback "
                 "(Phase 1 paper smoke); on real hardware set ASCEND_HOME + "
                 "ASCEND_RT_VISIBLE_DEVICES to auto-switch to live")
    }


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))

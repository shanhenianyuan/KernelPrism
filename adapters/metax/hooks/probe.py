#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""metax 适配器 probe（04 §3 接口契约）：设备探测。

返回:
  {available: bool, devices: [...], toolchain_ok: bool, versions: {...}}

Phase 1 纸面冒烟：本机无沐曦硬件时 available=false 但 toolchain_ok=template，
平台照常以通用兜底跑通 8 段。
真机部署（/opt/maca + /dev/mxc0）后此文件自动切换 live。
"""
import json
import os

from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent.parent
VENDORKB = os.path.join(ADAPTER_DIR, 'vendorkb')

# 沐曦工具链真身（对照实证）: /opt/maca/bin/mxcc（同 lscc 位）
MACA_REAL = os.environ.get('MACA_PATH') or '/opt/maca'
MXCC = os.path.join(MACA_REAL, 'bin', 'mxcc')
DEV_NODES = ['/dev/mxc0', '/dev/metax_gpu', '/dev/mxc']  # 待真机实证


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
        "note": ("本机未探测到沐曦工具链/设备 → 平台以通用兜底跑通 8 段"
                 "（Phase 1 纸面冒烟）；真机环境（/dev/mxc0 + /opt/maca/bin/mxcc）"
                 "自动切换 live" if not hw_available else
                 f"沐曦设备就绪: {','.join(DEV_NODES)}, mxcc={MXCC}")
    }


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))

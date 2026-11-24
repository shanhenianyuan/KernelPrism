#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lingspeed 适配器 probe（04 §3 接口契约）：设备探测。

返回:
  {available: bool, devices: [...], toolchain_ok: bool, versions: {...}}

Phase 1 纸面冒烟：本机无灵汐硬件时 available=false 但 toolchain_ok=template，
平台照常以通用兜底跑通 8 段。
真机部署（GPU 节点，/dev/lscd 已存在）后此文件自动切换 live。
"""
import json
import os
import sys

from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent.parent
VENDORKB = os.path.join(ADAPTER_DIR, 'vendorkb')

# 灵汐工具链真身（实证 2026-11）: /opt/luca symlink 悬空，必须用实链
LUCA_REAL = os.environ.get('LUCA_PATH') or '/opt/luca-3.8.0'
LSCC = os.path.join(LUCA_REAL, 'lsgpu_llvm', 'bin', 'lscc')
DEV_NODE = '/dev/lscd'  # crw------- 235,0，无需 mknod


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
        "note": ("本机未探测到灵汐工具链/设备 → 平台以通用兜底跑通 8 段"
                 "（Phase 1 纸面冒烟）；真机环境（/dev/lscd + /opt/luca-3.8.0）"
                 "自动切换 live" if not hw_available else
                 f"灵汐设备就绪: {DEV_NODE}, lscc={LSCC}")
    }


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))

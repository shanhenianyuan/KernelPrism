#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascend 适配器 probe（04 §3 接口契约）：设备探测。

返回:
  {available: bool, devices: [...], toolchain_ok: bool, versions: {...}}

Phase 1 纸面冒烟：本机无昇腾硬件时 available=false 但 toolchain_ok=template，
平台照常以通用兜底跑通 8 段（06-roadmap Phase 1 风险对策：纯 CPU sandbox 冒烟）。
真机部署后此文件由环境探测真实覆盖。
"""
import json
import os
import sys

from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent.parent
VENDORKB = os.path.join(ADAPTER_DIR, 'vendorkb')


def probe() -> dict:
    # 真实硬件探测点（昇腾环境变量惯用名）
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
        "note": ("本机未探测到昇腾工具链/设备 → 平台以通用兜底跑通 8 段"
                 "（Phase 1 纸面冒烟）；真机环境设置 ASCEND_HOME + "
                 "ASCEND_RT_VISIBLE_DEVICES 后自动切换 live")
    }


if __name__ == '__main__':
    print(json.dumps(probe(), ensure_ascii=False, indent=2))

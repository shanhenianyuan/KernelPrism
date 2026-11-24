#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lingspeed adapter status (04 §3 interface contract): capability status summary."""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from hooks.probe import probe  # noqa: E402


def status() -> dict:
    p = probe()
    vk_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vendorkb')
    vendorkb_chips = sorted(
        f[:-5] for f in os.listdir(vk_dir) if f.endswith('.json')
    ) if os.path.isdir(vk_dir) else []
    return {
        "vendor_id": "lingspeed",
        "available": p["available"],
        "capability_matrix": {
            "v0_understand": "platform-generic",
            "v1_analyze": "platform-generic + vendorkb",
            "v2_generate": "platform-generic (provider=generic)",
            "v3_compile": "platform-sandbox",
            "v4_verify": "platform-harness",
            "v5_profile": "platform-perf",
            "v6_optimize": "platform-evolver",
            "v7_register": "platform-registry"
        },
        "vendorkb_chips": vendorkb_chips,
        "license": "LUCA SDK Proprietary (lingspeed-device-only)",
        "paper_mode": p["paper_mode"]
    }


if __name__ == '__main__':
    print(json.dumps(status(), ensure_ascii=False, indent=2))

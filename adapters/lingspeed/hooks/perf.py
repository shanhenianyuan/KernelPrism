#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf.py — 灵汐 (Lingspeed) LUCA 适配器 · v5_profile hook（薄包装）

主体在 adapters/_common/mxcagen.py → run_perf()（平台参数化，lingspeed/metax 共用）。
输出契约不变：{timing: {per_iter_ms, std_ms, rounds, samples_used}, detail}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_perf()


if __name__ == '__main__':
    main()

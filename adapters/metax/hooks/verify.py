#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.py — 沐曦 (Metax MXMACA) 适配器 · v4_verify hook（薄包装）

主体在 adapters/_common/mxcagen.py → run_verify(name='metax')（平台参数化）。
输出契约与 lingspeed 对齐：{max_abs_err, max_rel_err, pass, detail}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_verify(name='metax')


if __name__ == '__main__':
    main()

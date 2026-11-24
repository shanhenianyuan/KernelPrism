#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile.py — 沐曦 (Metax MXMACA) 适配器 · v3_compile hook（薄包装）

主体在 adapters/_common/mxcagen.py → run_compile(name='metax')（平台参数化）。
输出契约与 lingspeed 对齐（BuildArtifact 兼容）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_compile(name='metax')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile.py — 灵汐 (Lingspeed) LUCA 适配器 · v3_compile hook（薄包装）

主体在 adapters/_common/mxcagen.py → run_compile()（平台参数化，lingspeed/metax 共用）。
本文件只负责把 pipeline 的 stdin JSON payload 转交 run_compile，并保持输出契约不变：
  BuildArtifact {build_ok, detail, build_log_path, artifacts:[{kind,ref}],
                 toolchain, env_snapshot, duration}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_compile()


if __name__ == '__main__':
    main()

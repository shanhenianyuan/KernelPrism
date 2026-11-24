#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile.py - Metax (MXMACA) adapter v3_compile hook (thin wrapper)

Core logic lives in adapters/_common/mxcagen.py -> run_compile(name='metax')
(platform-parameterized). Output contract aligned with lingspeed
(BuildArtifact compatible).
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
